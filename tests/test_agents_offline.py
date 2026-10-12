"""Drive the agents with a scripted model: checks the tool wiring without any API call."""

import asyncio
import csv
import json
import re

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent.agents.labeler as labeler_module
import agent.agents.reviewer as reviewer_module
from agent.agents.deps import Trace, Workspace
from agent.agents.labeler import ChunkDeps, run_labeler
from agent.core.labels import LabelStore
from agent.core.pieces import make_pieces
from agent.jobs import Job
from agent.orchestrator import RunConfig, run_job

from fixtures import mandala_spec

BOOK = """क ॥ १ ॥
टीका ॥ १ ॥
---
२७५ ग्रन्थनाम
## शीर्षकम्
ख ॥ २ ॥
टीका ॥ २ ॥
इति प्रथमः खण्डः
---
२७६ ग्रन्थनाम
ग ॥ १ ॥
टीका ॥ १ ॥
"""


def use_model(monkeypatch, function):
    for module in (labeler_module, reviewer_module):
        monkeypatch.setattr(module, "make_model", lambda model_id: FunctionModel(function))
        monkeypatch.setattr(module, "price", lambda model_id: None)


def turn(messages) -> int:
    return sum(isinstance(m, ModelResponse) for m in messages)


def rule_based_spans(prompt: str) -> list[dict]:
    """A stand-in for the model: label the rows under '## Pieces to label' with simple rules."""
    body = prompt.split("## Pieces to label")[1].split("## Context after")[0]
    spans, seen = [], set()
    for row in body.splitlines():
        m = re.match(r"(P\d+\w*) \| (.*?)(?:  \{(.*)\})?$", row)
        if not m:
            continue
        pid, text, hints = m.group(1), m.group(2), m.group(3) or ""
        number = re.search(r"n=(\d+)", hints)
        if "page-top-number" in hints:
            role, n = "page_header", []
        elif "md-heading" in hints:
            role, n = "heading", []
        elif text.startswith("इति "):
            role, n = "khanda_end", []
            seen.clear()
        else:
            k = int(number.group(1))
            role, n = ("bhashya" if k in seen else "mula"), [k]
            seen.add(k)
        spans.append({"start": pid, "end": pid, "role": role, "n": n})
    return spans


def test_labeler_fixes_its_chunk_after_finish_is_refused(monkeypatch, tmp_path):
    def script(messages, info):
        assert [t.name for t in info.function_tools] == ["label", "split_piece", "check", "preview", "flag"]
        assert [t.name for t in info.output_tools] == ["finish"]
        assert "# The Spec for this book" in info.instructions and "You label; you never write" in info.instructions
        step = turn(messages)
        if step == 0:  # forgets the page header
            spans = [s for s in rule_based_spans(messages[0].parts[-1].content) if s["role"] != "page_header"]
            return ModelResponse(parts=[ToolCallPart("label", {"spans": spans})])
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done"})])
        if step == 2:
            assert "Cannot finish yet" in messages[-1].parts[0].content and "P0003" in messages[-1].parts[0].content
            return ModelResponse(parts=[ToolCallPart("label", {"spans": [{"start": "P0003", "end": "P0003", "role": "page_header"}]})])
        return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done after fixing"})])

    use_model(monkeypatch, script)
    ws = Workspace(pieces=make_pieces(BOOK), store=LabelStore(), trace=Trace(tmp_path / "trace.jsonl"))
    deps = ChunkDeps(ws=ws, spec=mandala_spec(), first_page=1, last_page=2)
    outcome = asyncio.run(run_labeler(deps, "fake/model", "low"))
    assert outcome.ok and outcome.summary == "done after fixing" and outcome.requests == 4
    assert [r.id for r in deps.check().records] == ["1.1.1", "1.1.2"]
    tools = [json.loads(line)["tool"] for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert tools == ["label", "finish", "label", "finish", "run_end"]
    assert ws.usage.requests == 4


def test_labeler_that_never_finishes_is_reported_not_raised(monkeypatch):
    use_model(monkeypatch, lambda messages, info: ModelResponse(parts=[ToolCallPart("check", {})]))
    ws = Workspace(pieces=make_pieces(BOOK), store=LabelStore())
    outcome = asyncio.run(run_labeler(ChunkDeps(ws=ws, spec=mandala_spec(), first_page=1, last_page=1), "fake/model", "low", request_limit=5))
    assert not outcome.ok and outcome.summary.startswith("did not finish: UsageLimitExceeded")


def test_whole_job_runs_chunks_in_parallel_and_code_numbers_the_sections(monkeypatch, tmp_path):
    def script(messages, info):
        if turn(messages) == 0:
            return ModelResponse(parts=[ToolCallPart("label", {"spans": rule_based_spans(messages[0].parts[-1].content)})])
        return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done"})])

    use_model(monkeypatch, script)
    job = Job.create(BOOK, "one row per mantra", name="tiny", root=tmp_path)
    job.save_json("spec.json", mandala_spec().model_dump())
    summary = asyncio.run(run_job(job, RunConfig(chunk_pages=1, concurrency=3, auto_approve=True), say=lambda message: None))

    assert summary["state"] == "DONE" and summary["rows"] == 3 and summary["errors"] == 0
    assert [c["ok"] for c in summary["chunks"]] == [True, True, True]
    rows = list(csv.reader(open(job.path("output.csv"), encoding="utf-8", newline="")))
    assert rows == [
        ["num", "upanishad", "bhashya"],
        ["1.1.1", "क ॥ १ ॥", "टीका ॥ १ ॥"],
        ["1.1.2", "ख ॥ २ ॥", "शीर्षकम् टीका ॥ २ ॥ इति प्रथमः खण्डः"],
        ["1.2.1", "ग ॥ १ ॥", "टीका ॥ १ ॥"],
    ]
    assert job.path("output.csv").read_text(encoding="utf-8").startswith('"num","upanishad","bhashya"\n')
    for name in ("labels.json", "issues.json", "changes.json", "pieces.json", "state.json", "trace.jsonl"):
        assert job.has(name), name


def test_reviewer_is_called_when_the_book_has_errors(monkeypatch, tmp_path):
    def script(messages, info):
        tools = [t.name for t in info.function_tools]
        step = turn(messages)
        if "relabel" in tools:  # the Reviewer: give the closing line its proper role
            if step == 0:
                return ModelResponse(parts=[ToolCallPart("list_issues", {})])
            if step == 1:
                assert "field_number_backwards" in messages[-1].parts[0].content
                assert "FLAGGED [P0006]: not sure this is commentary" in messages[-1].parts[0].content
                return ModelResponse(parts=[ToolCallPart("relabel", {"spans": [{"start": "P0007", "end": "P0007", "role": "khanda_end"}]}),
                                            ToolCallPart("clear_flag", {"piece_ids": ["P0006"]})])
            return ModelResponse(parts=[ToolCallPart("finish", {"summary": "closing line relabelled"})])
        if step == 0:  # the Labeler: mislabels the closing line as commentary
            spans = rule_based_spans(messages[0].parts[-1].content)
            for s in spans:
                if s["role"] == "khanda_end":
                    s.update(role="bhashya", n=[2])
            calls = [ToolCallPart("label", {"spans": spans})]
            if any(s["start"] == "P0006" for s in spans):
                calls.append(ToolCallPart("flag", {"piece_ids": ["P0006"], "reason": "not sure this is commentary"}))
            return ModelResponse(parts=calls)
        return ModelResponse(parts=[ToolCallPart("finish", {"summary": "done"})])

    use_model(monkeypatch, script)
    job = Job.create(BOOK, "one row per mantra", name="tiny", root=tmp_path)
    job.save_json("spec.json", mandala_spec().model_dump())
    summary = asyncio.run(run_job(job, RunConfig(chunk_pages=1, auto_approve=True), say=lambda message: None))
    assert summary["state"] == "DONE" and summary["review_summary"] == "closing line relabelled"
    assert summary["flagged_pieces"] == 0
    assert [row[0] for row in csv.reader(open(job.path("output.csv"), encoding="utf-8", newline=""))][1:] == ["1.1.1", "1.1.2", "1.2.1"]
