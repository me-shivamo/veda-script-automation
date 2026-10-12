"""Web interface: load a book and a request, watch the agents work, approve the plan, read the output."""

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import AgentRunError

from agent.agents.analyst import AnalystResult
from agent.agents.models import DEFAULT_MODEL, ModelConfig
from agent.core.spec import render_spec
from agent.jobs import Job
from agent.orchestrator import RunConfig, run_job

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
EXAMPLES = ROOT / "examples"
JOBS = ROOT / "jobs"
JOB_ID = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]+$")
MAX_INPUT_CHARS = 5_000_000

# Only the first model has been tested on real books so far.
MODELS = [
    {"id": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna", "note": "tested"},
    {"id": "openai/gpt-6-luna", "label": "GPT-6 Luna", "note": "untested"},
    {"id": "anthropic/claude-haiku-5.5", "label": "Claude Haiku 5.5", "note": "untested"},
    {"id": "google/gemini-3.8-flash", "label": "Gemini 3.8 Flash", "note": "untested"},
    {"id": "anthropic/claude-sonnet-5.5", "label": "Claude Sonnet 5.5", "note": "untested, costs more"},
    {"id": "openai/gpt-5.6-sol", "label": "GPT-5.6 Sol", "note": "untested, costs more"},
]
STATE_TO_PHASE = {"DONE": "done", "NEEDS_HUMAN": "needs_review", "STOPPED": "stopped", "FAILED": "failed"}
ACTIVE = {"starting", "analysing", "awaiting_approval", "labelling", "reviewing", "assembling"}


@dataclass
class Run:
    """A run that this server process started. Finished runs are read back from their job folder."""

    job: Job
    phase: str = "starting"
    log: list[str] = field(default_factory=list)
    book: dict = field(default_factory=dict)
    chunks_done: int = 0
    chunks_total: int = 0
    proposal: dict | None = None
    decision: asyncio.Future | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.time)


RUNS: dict[str, Run] = {}


app = FastAPI(title="Veda Script Automation")


@app.middleware("http")
async def revalidate_pages(request, call_next):
    """Make browsers check for a newer page, script or stylesheet instead of using a stale copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class NewRun(BaseModel):
    name: str = Field(default="book", max_length=80)
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    request: str = Field(min_length=1, max_length=20_000)
    examples: str = Field(default="", max_length=200_000)
    model: str = Field(default=DEFAULT_MODEL, max_length=100)
    ask_approval: bool = True


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|revise|stop)$")
    feedback: str = Field(default="", max_length=5_000)


def _job(job_id: str) -> Job:
    if not JOB_ID.match(job_id) or not (JOBS / job_id).is_dir():
        raise HTTPException(404, "There is no run with that ID.")
    return Job(JOBS / job_id)


def _explain(error: Exception) -> str:
    text = str(error)
    if "OPENROUTER_API_KEY" in text:
        return "The OpenRouter API key is not set. Put OPENROUTER_API_KEY in the .env file and restart the server."
    if "401" in text or "User not found" in text:
        return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in the .env file."
    if "402" in text or "credits" in text.lower():
        return "The OpenRouter account is out of credits."
    if isinstance(error, AgentRunError):
        return f"An agent could not finish its work: {text[:300]}"
    return f"{type(error).__name__}: {text[:300]}"


async def _drive(run: Run, config: RunConfig) -> None:
    def on_event(event: dict) -> None:
        if event["type"] == "book":
            run.book = event
        elif event["type"] == "phase":
            run.phase = event["phase"]
            run.chunks_total = event.get("total", run.chunks_total)
        elif event["type"] == "chunk":
            run.chunks_done, run.chunks_total = event["done"], event["total"]

    async def approve(result: AnalystResult) -> bool | str:
        run.proposal = {
            "summary": result.summary, "limitations": result.limitations, "columns": result.spec.columns,
            "rows": result.preview_rows[:12], "rules": render_spec(result.spec),
        }
        run.decision = asyncio.get_running_loop().create_future()
        run.phase = "awaiting_approval"
        try:
            return await run.decision
        finally:
            run.decision, run.proposal = None, None

    try:
        summary = await run_job(run.job, config, approve=approve, say=run.log.append, on_event=on_event)
        run.phase = STATE_TO_PHASE[summary["state"]]
    except asyncio.CancelledError:
        run.phase = "stopped"
        run.job.save_json("state.json", {"state": "STOPPED", "reason": "stopped before it finished"})
        raise
    except Exception as error:  # report every failure to the page instead of losing it in the server log
        run.phase, run.error = "failed", _explain(error)
        run.job.save_json("state.json", {"state": "FAILED", "error": run.error})


def _meta(job: Job) -> dict:
    if job.has("meta.json"):
        return job.load_json("meta.json")
    # A run started from the command line has no meta file; read what we can from the folder name and results.
    stamp, name = job.dir.name[:15], job.dir.name[16:]
    return {"name": name, "model": _state(job).get("models", {}).get("labeler", ""), "created": time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))}


def _state(job: Job) -> dict:
    return job.load_json("state.json") if job.has("state.json") else {}


def _phase(job: Job) -> str:
    run = RUNS.get(job.dir.name)
    if run:
        return run.phase
    # A job folder with no result and no live run was cut off, e.g. by a server restart.
    return STATE_TO_PHASE.get(_state(job).get("state", ""), "interrupted")


def _card(job: Job) -> dict:
    meta, state = _meta(job), _state(job)
    return {
        "id": job.dir.name, "name": meta["name"], "model": meta["model"], "created": meta["created"], "phase": _phase(job),
        "rows": state.get("rows"), "cost_usd": state.get("cost_usd"), "seconds": state.get("seconds"),
    }


@app.get("/api/config")
def config() -> dict:
    samples = [{"id": d.name, "name": d.name.replace("-", " ").title()} for d in sorted(EXAMPLES.glob("*"))
               if (d / "input.md").exists() and (d / "request.txt").exists()]
    return {"default_model": DEFAULT_MODEL, "models": MODELS, "key_configured": bool(os.environ.get("OPENROUTER_API_KEY")), "samples": samples}


@app.get("/healthz")
def health() -> dict:
    """For the host's health check. Never asks for a password."""
    return {"ok": True}


@app.get("/api/samples/{sample_id}")
def sample(sample_id: str) -> dict:
    folder = EXAMPLES / sample_id
    if not re.fullmatch(r"[a-z0-9-]+", sample_id) or not (folder / "input.md").exists():
        raise HTTPException(404, "There is no sample with that name.")
    read = lambda name: (folder / name).read_text(encoding="utf-8") if (folder / name).exists() else ""  # noqa: E731
    return {"name": sample_id, "input": read("input.md"), "request": read("request.txt"), "examples": read("example_rows.csv")}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    folders = sorted((d for d in JOBS.glob("*") if d.is_dir() and JOB_ID.match(d.name)), reverse=True)
    return [_card(Job(d)) for d in folders[:40]]


@app.post("/api/runs")
async def start_run(body: NewRun) -> dict:
    if not any(m["id"] == body.model for m in MODELS):
        raise HTTPException(422, "That model is not in the list.")
    job = Job.create(body.input, body.request, examples=body.examples, name=body.name, root=JOBS)
    job.save_json("meta.json", {"name": body.name.strip() or "book", "model": body.model, "created": time.time()})
    run = Run(job=job)
    RUNS[job.dir.name] = run
    run.task = asyncio.create_task(_drive(run, RunConfig(models=ModelConfig.all(body.model), auto_approve=not body.ask_approval)))
    return {"id": job.dir.name}


@app.get("/api/runs/{job_id}")
def run_status(job_id: str) -> dict:
    job = _job(job_id)
    run, state = RUNS.get(job_id), _state(job)
    status = _card(job) | {
        "active": _phase(job) in ACTIVE,
        "error": run.error if run else state.get("error"),
        "log": run.log[-200:] if run else [],
        "book": run.book if run else {},
        "chunks_done": run.chunks_done if run else 0,
        "chunks_total": run.chunks_total if run else 0,
        "proposal": run.proposal if run else None,
        "elapsed": round(time.time() - run.started) if run and _phase(job) in ACTIVE else state.get("seconds"),
        "has_output": job.has("output.json"),
    }
    return status


@app.post("/api/runs/{job_id}/decision")
async def decide(job_id: str, body: Decision) -> dict:
    run = RUNS.get(_job(job_id).dir.name)
    if run is None or run.decision is None or run.decision.done():
        raise HTTPException(409, "This run is not waiting for a decision.")
    if body.action == "revise" and not body.feedback.strip():
        raise HTTPException(422, "Say what should change.")
    run.decision.set_result({"approve": True, "stop": False}.get(body.action, body.feedback.strip()))
    run.phase = {"approve": "labelling", "revise": "analysing", "stop": "stopped"}[body.action]
    return {"ok": True}


@app.post("/api/runs/{job_id}/stop")
async def stop(job_id: str) -> dict:
    run = RUNS.get(_job(job_id).dir.name)
    if run is None or run.task is None or run.task.done():
        raise HTTPException(409, "This run is not in progress.")
    run.task.cancel()
    return {"ok": True}


@app.get("/api/runs/{job_id}/input")
def run_input(job_id: str) -> dict:
    job = _job(job_id)
    return {"name": _meta(job)["name"], "input": job.read("input.md"), "request": job.read("request.txt"), "examples": job.read("examples.csv")}


@app.get("/api/runs/{job_id}/result")
def run_result(job_id: str) -> dict:
    job = _job(job_id)
    if not job.has("output.json"):
        raise HTTPException(404, "This run has no output yet.")
    records = job.load_json("output.json")
    issues = job.load_json("issues.json") if job.has("issues.json") else []
    changes = job.load_json("changes.json") if job.has("changes.json") else []
    columns = [k for k in records[0] if k != "_pieces"] if records else []
    row_of = {piece: r[columns[0]] for r in records for ids in r["_pieces"].values() for piece in ids}
    notes: dict[str, list[str]] = {}
    for issue in issues:
        issue["row"] = next((row_of[p] for p in issue["pieces"] if p in row_of), (issue.get("unit") or "").split("/")[0] or None)
        if issue["row"]:
            notes.setdefault(issue["row"], []).append(issue["message"])
    for change in changes:
        change["row"] = row_of.get(change["piece"])
    rows = [{"id": r[columns[0]], "cells": [r[c] for c in columns[1:]], "notes": notes.get(r[columns[0]], [])} for r in records]
    return {"columns": columns, "rows": rows, "issues": issues, "changes": changes, "summary": _state(job), "card": _card(job)}


@app.get("/api/runs/{job_id}/download/{kind}")
def download(job_id: str, kind: str) -> FileResponse:
    job = _job(job_id)
    if kind not in ("csv", "json") or not job.has(f"output.{kind}"):
        raise HTTPException(404, "That file does not exist.")
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", _meta(job)["name"]).strip("-") or "output"
    return FileResponse(job.path(f"output.{kind}"), filename=f"{name}.{kind}", media_type="text/csv" if kind == "csv" else "application/json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
