"""The web interface end to end, with scripted models: start a run, approve the plan, read the result."""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent.agents.analyst as analyst_module
import agent.agents.labeler as labeler_module
import agent.agents.reviewer as reviewer_module
import web.app as web

from fixtures import mandala_spec
from test_agents_offline import BOOK, rule_based_spans, turn


def scripted(messages, info):
    tools = [t.name for t in info.function_tools]
    step = turn(messages)
    if "draft_spec" in tools:  # the Analyst: look, draft, try the draft on a page, submit
        calls = [
            ToolCallPart("overview", {}),
            ToolCallPart("draft_spec", {"spec": mandala_spec().model_dump()}),
            ToolCallPart("trial_run", {"first_page": 1, "last_page": 2}),
            ToolCallPart("submit", {"summary": "One row per mantra with its commentary.", "limitations": ["No translation is printed."],
                                    "outline": [{"path": "1.1", "label": "प्रथमः खण्डः", "evidence": ["P0001"], "units": "1–2"}],
                                    "questions": ["Keep the closing line in the commentary?"]}),
        ]
        return ModelResponse(parts=[calls[min(step, 3)]])
    if step == 0:  # a Labeler
        return ModelResponse(parts=[ToolCallPart("label", {"spans": rule_based_spans(messages[0].parts[-1].content)})])
    return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done"})])


@pytest.fixture
def scripted_app(monkeypatch, tmp_path):
    """The web app with scripted models and an empty jobs folder, not started yet."""
    for module in (analyst_module, labeler_module, reviewer_module):
        monkeypatch.setattr(module, "make_model", lambda model_id: FunctionModel(scripted))
        monkeypatch.setattr(module, "price", lambda model_id: None)
    monkeypatch.setattr(web, "JOBS", tmp_path)
    monkeypatch.setattr(web, "RUNS", {})
    monkeypatch.setattr(web, "STORE", None)
    return web.app


@pytest.fixture
def client(scripted_app):
    with TestClient(scripted_app) as test_client:
        yield test_client


def wait_for(client, run_id, phases, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()
        if status["phase"] in phases:
            return status
        time.sleep(0.05)
    raise AssertionError(f"run stayed in phase {status['phase']}: {status}")


def start(client, **extra):
    body = {"name": "Tiny Book", "input": BOOK, "request": "one row per mantra", "model": "openai/gpt-6-luna"} | extra
    response = client.post("/api/runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_page_and_config_are_served(client):
    assert "Veda Script Automation" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    config = client.get("/api/config").json()
    assert config["default_model"] == "openai/gpt-6-luna" and config["models"][0]["id"] == config["default_model"]
    assert any(sample["id"] == "mandala-brahmana" for sample in config["samples"])
    assert client.get("/api/samples/mandala-brahmana").json()["request"].startswith("This is the Mandala Brahmana")


def test_run_waits_for_approval_then_produces_rows(client):
    run_id = start(client)
    status = wait_for(client, run_id, {"awaiting_approval"})
    proposal = status["proposal"]
    assert proposal["summary"] == "One row per mantra with its commentary."
    assert proposal["limitations"] == ["No translation is printed."]
    assert proposal["columns"] == ["num", "upanishad", "bhashya"]
    assert proposal["version"] == 1 and proposal["questions"] == ["Keep the closing line in the commentary?"]
    assert proposal["outline"][0]["evidence"][0]["found"] and proposal["structure"]["levels"][0]["name"] == "brahmana"
    preview = proposal["preview"]
    assert preview["table"]["columns"] == ["num", "upanishad", "bhashya"] and [row[0] for row in preview["table"]["rows"]] == ["1.1.1", "1.1.2"]
    assert preview["blocks"]["columns"][-2:] == ["layer", "text"] and "brahmanas" in preview["tree"]
    assert "Roles" in proposal["rules"]

    # A change to the output structure is made by code: no agent runs, and the plan comes back as version 2.
    bad = client.post(f"/api/runs/{run_id}/decision", json={"action": "edit", "edits": [{"op": "rename_column", "column": "bhashya", "to": "upanishad"}]})
    assert bad.status_code == 422 and "cannot be a column name" in bad.json()["detail"]
    edit = {"action": "edit", "edits": [{"op": "rename_column", "column": "upanishad", "to": "mula"}, {"op": "set_output", "id_style": "columns"}]}
    assert client.post(f"/api/runs/{run_id}/decision", json=edit).json() == {"ok": True}
    deadline = time.time() + 10
    while (proposal := client.get(f"/api/runs/{run_id}").json()["proposal"]) is None or proposal["version"] != 2:
        assert time.time() < deadline
        time.sleep(0.05)
    assert proposal["columns"] == ["num", "mula", "bhashya"]
    assert proposal["preview"]["table"]["columns"] == ["brahmana", "khanda", "mantra", "mula", "bhashya"]
    assert "Column upanishad is now called mula." in proposal["changes"]
    assert [plan["version"] for plan in client.get(f"/api/runs/{run_id}/plans").json()] == [1, 2]

    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "revise", "feedback": ""}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"}).json() == {"ok": True}
    status = wait_for(client, run_id, {"done", "needs_review", "failed"})
    assert status["phase"] == "done" and status["has_output"] and not status["active"]
    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"}).status_code == 409

    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["columns"] == ["num", "mula", "bhashya"]
    assert result["shapes"] == {"table": True, "blocks": True, "tree": True} and result["plan"]["version"] == 2
    assert [m["mantra"] for m in result["tree"]["brahmanas"][0]["khandas"][0]["mantras"]] == [1, 2]
    assert [row["id"] for row in result["rows"]] == ["1.1.1", "1.1.2", "1.2.1"]
    assert result["rows"][1]["cells"] == ["ख ॥ २ ॥", "शीर्षकम् टीका ॥ २ ॥ इति प्रथमः खण्डः"]
    assert result["issues"] == [] and result["card"]["name"] == "Tiny Book"

    csv_file = client.get(f"/api/runs/{run_id}/download/csv")
    assert csv_file.text.startswith('"brahmana","khanda","mantra","mula","bhashya"\n') and 'filename="Tiny-Book.csv"' in csv_file.headers["content-disposition"]
    blocks = client.get(f"/api/runs/{run_id}/download/blocks")
    assert blocks.text.startswith('"path","brahmana","khanda","mantra","layer","text"\n') and "Tiny-Book-blocks.csv" in blocks.headers["content-disposition"]
    assert client.get(f"/api/runs/{run_id}/download/tree").json()["structure"]["unit"] == "mantra"
    assert client.get(f"/api/runs/{run_id}/input").json()["input"] == BOOK
    assert client.get("/api/runs").json()[0] | {"created": 0, "seconds": 0, "cost_usd": 0} == {
        "id": run_id, "name": "Tiny Book", "model": "openai/gpt-6-luna", "created": 0, "phase": "done", "rows": 3, "cost_usd": 0, "seconds": 0}


def test_feedback_sends_the_plan_back_and_stop_ends_the_run(client):
    run_id = start(client)
    wait_for(client, run_id, {"awaiting_approval"})
    client.post(f"/api/runs/{run_id}/decision", json={"action": "revise", "feedback": "Add a column."})
    wait_for(client, run_id, {"awaiting_approval"})  # the Analyst ran again and asks once more
    client.post(f"/api/runs/{run_id}/decision", json={"action": "stop"})
    status = wait_for(client, run_id, {"stopped"})
    assert not status["has_output"]
    assert client.get(f"/api/runs/{run_id}/result").status_code == 404


def test_run_without_approval_step_and_bad_requests(client):
    run_id = start(client, ask_approval=False, output={"shape": "tree", "columns": ["num", "mula", "bhashya"]})
    assert wait_for(client, run_id, {"done", "failed"})["phase"] == "done"
    request = client.get(f"/api/runs/{run_id}/input").json()["request"]
    assert "Output shape: a tree" in request and "Columns, in this order: num, mula, bhashya." in request
    assert client.post("/api/runs", json={"input": "x", "request": "x", "output": {"shape": "pyramid"}}).status_code == 422
    assert client.post("/api/runs", json={"input": "", "request": "x"}).status_code == 422
    assert client.post("/api/runs", json={"input": "x", "request": "x", "model": "made/up"}).status_code == 422
    assert client.get("/api/runs/../../etc").status_code == 404
    assert client.get("/api/runs/20200101-000000-missing").status_code == 404


def basic(username, password):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def test_password_protects_everything_except_the_health_check(client, monkeypatch):
    monkeypatch.setattr(web, "APP_PASSWORD", "open sesame")
    for path in ("/", "/api/config", "/api/runs", "/static/app.js"):
        response = client.get(path)
        assert response.status_code == 401 and response.headers["www-authenticate"].startswith("Basic"), path
    assert client.post("/api/runs", json={"input": "x", "request": "x"}).status_code == 401
    assert client.get("/healthz").json() == {"ok": True}
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef")
    assert client.get("/healthz").json() == {"ok": True, "commit": "0123456"}
    for wrong in (basic("veda", "wrong"), basic("someone", "open sesame"), {"Authorization": "Basic not-base64!"}, {"Authorization": "Bearer open sesame"}):
        assert client.get("/api/config", headers=wrong).status_code == 401
    assert client.get("/api/config", headers=basic("veda", "open sesame")).status_code == 200
    assert "Veda Script Automation" in client.get("/", headers=basic("veda", "open sesame")).text


def test_no_password_means_no_prompt_and_health_check_always_answers(client, monkeypatch):
    assert client.get("/healthz").json() == {"ok": True}
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123456789abcdef")
    assert client.get("/healthz").json() == {"ok": True, "commit": "0123456"}
    assert client.get("/api/config").json()["temporary_storage"] is False


def test_only_a_few_runs_at_once(client, monkeypatch):
    monkeypatch.setattr(web, "MAX_ACTIVE_RUNS", 1)
    first = start(client)
    wait_for(client, first, {"awaiting_approval"})
    refused = client.post("/api/runs", json={"name": "second", "input": BOOK, "request": "one row per mantra"})
    assert refused.status_code == 429 and "already in progress" in refused.json()["detail"]
    client.post(f"/api/runs/{first}/decision", json={"action": "stop"})
    wait_for(client, first, {"stopped"})
    assert client.post("/api/runs", json={"name": "second", "input": BOOK, "request": "one row per mantra"}).status_code == 200
