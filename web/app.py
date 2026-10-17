"""Web interface: load a book and a request, watch the agents work, approve the plan, read the output."""

import asyncio
import base64
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import AgentRunError

from agent.agents.analyst import AnalystResult
from agent.agents.models import DEFAULT_MODEL, ModelConfig
from agent.core.spec import Spec, StructureEdit, apply_structure_edits
from agent.jobs import Job
from agent.orchestrator import RunConfig, run_job
from web.store import Store

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
EXAMPLES = ROOT / "examples"

# Settings for a hosted copy, all optional. See docs/DOCUMENTATION.md, "Settings".
JOBS = Path(os.environ.get("JOBS_DIR") or ROOT / "jobs")  # where runs are kept; point it at a persistent disk
APP_USERNAME = os.environ.get("APP_USERNAME", "veda")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")  # when set, every page and API call asks for it
MAX_ACTIVE_RUNS = int(os.environ.get("MAX_ACTIVE_RUNS", "3"))
# A Postgres database that keeps a copy of every run, for a host that wipes its files (see web/store.py).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
STORE = Store(DATABASE_URL, JOBS) if DATABASE_URL else None
# Render's free plan wipes local files on every restart, so without a disk or a database the page warns people.
TEMPORARY_STORAGE = bool(os.environ.get("RENDER")) and not os.environ.get("JOBS_DIR") and not DATABASE_URL
JOB_ID = re.compile(r"^\d{8}-\d{6}-[a-z0-9-]+$")
MAX_INPUT_CHARS = 5_000_000

# The default comes first. Only the first two have been tested on real books so far.
MODELS = [
    {"id": "openai/gpt-6-luna", "label": "GPT-6 Luna", "note": "tested"},
    {"id": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna", "note": "tested"},
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
    spec: Spec | None = None  # the Spec of the plan waiting for approval
    decision: asyncio.Future | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.time)


RUNS: dict[str, Run] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    JOBS.mkdir(parents=True, exist_ok=True)
    if STORE:
        await asyncio.to_thread(STORE.start)  # a wrong DATABASE_URL stops the server here, where the host's log shows it
    yield
    # On shutdown, stop the runs still in progress so each one records that it was stopped.
    tasks = [run.task for run in RUNS.values() if run.task and not run.task.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Veda Script Automation", lifespan=lifespan)


def _signed_in(header: str) -> bool:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        username, _, password = base64.b64decode(encoded).decode("utf-8").partition(":")
    except ValueError:
        return False
    # Compare both in constant time so the check does not leak how much of a guess was right.
    return secrets.compare_digest(username.encode(), APP_USERNAME.encode()) & secrets.compare_digest(password.encode(), APP_PASSWORD.encode())


@app.middleware("http")
async def require_password(request, call_next):
    """With APP_PASSWORD set, the browser asks for a username and password before showing anything."""
    if APP_PASSWORD and request.url.path != "/healthz" and not _signed_in(request.headers.get("authorization", "")):
        return PlainTextResponse("Sign in to continue.", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="Veda Script Automation", charset="UTF-8"'})
    return await call_next(request)


@app.middleware("http")
async def revalidate_pages(request, call_next):
    """Make browsers check for a newer page, script or stylesheet instead of using a stale copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class OutputWish(BaseModel):
    """The output structure asked for on the request form, before the Analyst starts. Every part is optional."""

    shape: str = Field(default="", pattern="^(|table|blocks|tree)$")
    id_style: str = Field(default="", pattern="^(|dotted|columns)$")
    columns: list[str] = Field(default_factory=list, max_length=30)
    section_text: str = Field(default="", pattern="^(|merge|rows|columns)$")


class NewRun(BaseModel):
    name: str = Field(default="book", max_length=80)
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    request: str = Field(min_length=1, max_length=20_000)
    examples: str = Field(default="", max_length=200_000)
    model: str = Field(default=DEFAULT_MODEL, max_length=100)
    ask_approval: bool = True
    output: OutputWish = Field(default_factory=OutputWish)


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|revise|edit|stop)$")
    feedback: str = Field(default="", max_length=5_000)
    answers: dict[str, str] = Field(default_factory=dict, max_length=30)
    edits: list[StructureEdit] = Field(default_factory=list, max_length=50)


def _with_output_wish(request: str, wish: OutputWish) -> str:
    """The form's structure choices, added to the request in words the Analyst reads."""
    lines = []
    shapes = {"table": "a table, one row per unit with one column per kind of text",
              "blocks": "blocks, one row per piece of text with its path, level numbers, unit number, layer and text",
              "tree": "a tree, nested JSON in the book's own shape"}
    if wish.shape:
        lines.append(f"- Output shape: {shapes[wish.shape]} (output.shape = '{wish.shape}').")
    if wish.id_style:
        lines.append("- Row ID: " + ("one dotted ID such as 1.3.5." if wish.id_style == "dotted" else "one column per section level plus the unit number.")
                     + f" (output.id_style = '{wish.id_style}')")
    columns = [c.strip() for c in wish.columns if c.strip()]
    if columns:
        lines.append("- Columns, in this order: " + ", ".join(columns) + ".")
    if wish.section_text:
        lines.append({"merge": "- Section titles and closing lines go into the text of the unit next to them.",
                      "rows": "- Section titles and closing lines are kept apart, as a row of their own for each section.",
                      "columns": "- Section titles and closing lines are kept apart, in their own columns on every row of the section."}[wish.section_text])
    return request if not lines else request.rstrip() + "\n\nOutput structure chosen on the form:\n" + "\n".join(lines) + "\n"


def _job(job_id: str) -> Job:
    if not JOB_ID.match(job_id):
        raise HTTPException(404, "There is no run with that ID.")
    if STORE:
        STORE.fetch(job_id)
    if not (JOBS / job_id).is_dir():
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


async def _keep(run: Run) -> None:
    """Copy the run's folder to the database, if there is one. A failure is shown on the page instead of ending the run."""
    if STORE is None:
        return
    try:
        await asyncio.to_thread(STORE.save, run.job.dir)
    except Exception as error:  # a database problem must not lose the run itself
        run.log.append(f"Could not save this run to the database, so a server restart will lose it. {type(error).__name__}: {str(error)[:300]}")


async def _drive(run: Run, config: RunConfig) -> None:
    def on_event(event: dict) -> None:
        if event["type"] == "book":
            run.book = event
        elif event["type"] == "phase":
            run.phase = event["phase"]
            run.chunks_total = event.get("total", run.chunks_total)
        elif event["type"] == "chunk":
            run.chunks_done, run.chunks_total = event["done"], event["total"]

    async def approve(result: AnalystResult, view: dict) -> bool | str | dict:
        run.proposal = view
        run.spec = result.spec
        await _keep(run)  # each version of the plan is kept, so it can be read again after a restart
        run.decision = asyncio.get_running_loop().create_future()
        run.phase = "awaiting_approval"
        try:
            return await run.decision
        finally:
            run.decision, run.proposal, run.spec = None, None, None

    try:
        await _keep(run)  # the input is saved first, so a run cut off by a crash still shows what was sent
        summary = await run_job(run.job, config, approve=approve, say=run.log.append, on_event=on_event)
        run.phase = STATE_TO_PHASE[summary["state"]]
    except asyncio.CancelledError:
        run.phase = "stopped"
        run.job.save_json("state.json", {"state": "STOPPED", "reason": "stopped before it finished"})
        raise
    except Exception as error:  # report every failure to the page instead of losing it in the server log
        run.phase, run.error = "failed", _explain(error)
        run.job.save_json("state.json", {"state": "FAILED", "error": run.error})
    finally:
        await _keep(run)  # also when the server is shutting down: it cancels the run, and the run is kept as stopped


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
    return {"default_model": DEFAULT_MODEL, "models": MODELS, "key_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
            "samples": samples, "temporary_storage": TEMPORARY_STORAGE}


@app.get("/healthz")
def health() -> dict:
    """For the host's health check. Never asks for a password. Names the deployed commit (Render sets RENDER_GIT_COMMIT)."""
    commit = os.environ.get("RENDER_GIT_COMMIT", "")[:7]
    return {"ok": True, "commit": commit} if commit else {"ok": True}


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
    active = sum(1 for run in RUNS.values() if run.phase in ACTIVE)
    if active >= MAX_ACTIVE_RUNS:
        raise HTTPException(429, f"{active} runs are already in progress. Wait for one to finish, then try again.")
    job = Job.create(body.input, _with_output_wish(body.request, body.output), examples=body.examples, name=body.name, root=JOBS)
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
    answers = {q: a.strip() for q, a in body.answers.items() if a.strip()}
    if body.action == "revise" and not body.feedback.strip() and not answers and not body.edits:
        raise HTTPException(422, "Say what should change, or answer a question.")
    if body.action == "edit" and not body.edits:
        raise HTTPException(422, "There is no change to the structure.")
    if body.edits and run.spec is not None:
        try:
            apply_structure_edits(run.spec, body.edits)  # refuse a bad edit here, where the page can show why
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    if body.action == "approve":
        result = True
    elif body.action == "stop":
        result = False
    else:
        result = {"feedback": body.feedback.strip() if body.action == "revise" else "", "answers": answers if body.action == "revise" else {},
                  "edits": [e.model_dump() for e in body.edits]}
    run.decision.set_result(result)
    ai = body.action == "revise" and (body.feedback.strip() or answers)
    run.phase = {"approve": "labelling", "stop": "stopped"}.get(body.action, "analysing" if ai else "awaiting_approval")
    return {"ok": True}


@app.get("/api/runs/{job_id}/plans")
def plans(job_id: str) -> list[dict]:
    """Every version of the plan this run was shown, oldest first."""
    job = _job(job_id)
    versions = sorted((int(m.group(1)), name.name) for name in job.dir.glob("plan_*.json") if (m := re.fullmatch(r"plan_(\d+)\.json", name.name)))
    return [job.load_json(name) for _, name in versions]


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


def _removed(job: Job) -> list[dict]:
    """The lines left out of the output. Runs made before removed.json existed are worked out from their labels."""
    if job.has("removed.json"):
        return job.load_json("removed.json")
    if not all(job.has(name) for name in ("pieces.json", "labels.json", "spec.json")):
        return []
    drop = {r["name"] for r in job.load_json("spec.json")["roles"] if r["kind"] == "drop"}
    labels = job.load_json("labels.json")["labels"]
    return [{"piece": p["id"], "page": p["page"], "role": labels[p["id"]]["role"], "text": p["text"]}
            for p in job.load_json("pieces.json") if labels.get(p["id"], {}).get("role") in drop]


@app.get("/api/runs/{job_id}/result")
def run_result(job_id: str) -> dict:
    job = _job(job_id)
    if not job.has("output.json"):
        raise HTTPException(404, "This run has no output yet.")
    records = job.load_json("output.json")
    issues = job.load_json("issues.json") if job.has("issues.json") else []
    removed = _removed(job)
    columns = [k for k in records[0] if k != "_pieces"] if records else []
    row_of = {piece: r[columns[0]] for r in records for ids in r["_pieces"].values() for piece in ids}
    notes: dict[str, list[str]] = {}
    for issue in issues:
        issue["row"] = next((row_of[p] for p in issue["pieces"] if p in row_of), (issue.get("unit") or "").split("/")[0] or None)
        if issue["row"]:
            notes.setdefault(issue["row"], []).append(issue["message"])
    rows = [{"id": r[columns[0]], "cells": [r[c] for c in columns[1:]], "notes": notes.get(r[columns[0]], [])} for r in records]
    spec = job.load_json("spec.json") if job.has("spec.json") else {}
    sections = job.load_json("sections.json") if job.has("sections.json") else []
    shapes = {"table": job.has("output.csv"), "blocks": job.has("output_blocks.csv"), "tree": job.has("output_tree.json")}
    return {"columns": columns, "rows": rows, "issues": issues, "removed": removed, "summary": _state(job), "card": _card(job),
            "sections": sections, "shape": (spec.get("output") or {}).get("shape", "table"), "shapes": shapes,
            "tree": job.load_json("output_tree.json") if job.has("output_tree.json") else None,
            "plan": job.load_json("plan.json") if job.has("plan.json") else None}


@app.get("/api/runs/{job_id}/download/{kind}")
def download(job_id: str, kind: str) -> FileResponse:
    job = _job(job_id)
    files = {"csv": ("output.csv", "", "csv"), "json": ("output.json", "", "json"),
             "blocks": ("output_blocks.csv", "-blocks", "csv"), "tree": ("output_tree.json", "-tree", "json")}
    if kind not in files or not job.has(files[kind][0]):
        raise HTTPException(404, "That file does not exist.")
    source, suffix, ext = files[kind]
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", _meta(job)["name"]).strip("-") or "output"
    return FileResponse(job.path(source), filename=f"{name}{suffix}.{ext}", media_type="text/csv" if ext == "csv" else "application/json")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
