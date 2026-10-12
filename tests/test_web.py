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
            ToolCallPart("submit", {"summary": "One row per mantra with its commentary.", "limitations": ["No translation is printed."]}),
        ]
        return ModelResponse(parts=[calls[min(step, 3)]])
    if step == 0:  # a Labeler
        return ModelResponse(parts=[ToolCallPart("label", {"spans": rule_based_spans(messages[0].parts[-1].content)})])
    return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done"})])


@pytest.fixture
def client(monkeypatch, tmp_path):
    for module in (analyst_module, labeler_module, reviewer_module):
        monkeypatch.setattr(module, "make_model", lambda model_id: FunctionModel(scripted))
        monkeypatch.setattr(module, "price", lambda model_id: None)
    monkeypatch.setattr(web, "JOBS", tmp_path)
    monkeypatch.setattr(web, "RUNS", {})
    with TestClient(web.app) as test_client:
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
    body = {"name": "Tiny Book", "input": BOOK, "request": "one row per mantra", "model": "openai/gpt-5.6-luna"} | extra
    response = client.post("/api/runs", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_page_and_config_are_served(client):
    assert "Veda Script Automation" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    config = client.get("/api/config").json()
    assert config["default_model"] == "openai/gpt-5.6-luna" and config["models"][0]["id"] == config["default_model"]
    assert any(sample["id"] == "mandala-brahmana" for sample in config["samples"])
    assert client.get("/api/samples/mandala-brahmana").json()["request"].startswith("This is the Mandala Brahmana")


def test_run_waits_for_approval_then_produces_rows(client):
    run_id = start(client)
    status = wait_for(client, run_id, {"awaiting_approval"})
    proposal = status["proposal"]
    assert proposal["summary"] == "One row per mantra with its commentary."
    assert proposal["limitations"] == ["No translation is printed."]
    assert proposal["columns"] == ["num", "upanishad", "bhashya"]
    assert [row["label"] for row in proposal["rows"]] == ["mantra 1", "mantra 2"]
    assert "Roles" in proposal["rules"]

    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "revise", "feedback": ""}).status_code == 422
    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"}).json() == {"ok": True}
    status = wait_for(client, run_id, {"done", "needs_review", "failed"})
    assert status["phase"] == "done" and status["has_output"] and not status["active"]
    assert client.post(f"/api/runs/{run_id}/decision", json={"action": "approve"}).status_code == 409

    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["columns"] == ["num", "upanishad", "bhashya"]
    assert [row["id"] for row in result["rows"]] == ["1.1.1", "1.1.2", "1.2.1"]
    assert result["rows"][1]["cells"] == ["ख ॥ २ ॥", "शीर्षकम् टीका ॥ २ ॥ इति प्रथमः खण्डः"]
    assert result["issues"] == [] and result["card"]["name"] == "Tiny Book"

    csv_file = client.get(f"/api/runs/{run_id}/download/csv")
    assert csv_file.text.startswith('"num","upanishad","bhashya"\n') and 'filename="Tiny-Book.csv"' in csv_file.headers["content-disposition"]
    assert client.get(f"/api/runs/{run_id}/input").json()["input"] == BOOK
    assert client.get("/api/runs").json()[0] | {"created": 0, "seconds": 0, "cost_usd": 0} == {
        "id": run_id, "name": "Tiny Book", "model": "openai/gpt-5.6-luna", "created": 0, "phase": "done", "rows": 3, "cost_usd": 0, "seconds": 0}


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
    run_id = start(client, ask_approval=False)
    assert wait_for(client, run_id, {"done", "failed"})["phase"] == "done"
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
    for wrong in (basic("veda", "wrong"), basic("someone", "open sesame"), {"Authorization": "Basic not-base64!"}, {"Authorization": "Bearer open sesame"}):
        assert client.get("/api/config", headers=wrong).status_code == 401
    assert client.get("/api/config", headers=basic("veda", "open sesame")).status_code == 200
    assert "Veda Script Automation" in client.get("/", headers=basic("veda", "open sesame")).text
