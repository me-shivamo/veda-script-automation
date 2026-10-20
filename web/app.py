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

from anthropic import AsyncAnthropic, AuthenticationError
from dotenv import set_key, unset_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError

from agent.agents.analyst import AnalystResult
from agent.agents.models import DEFAULT_MODEL, ModelConfig, api_model
from agent.agents.subscription import PREFIX as SUBSCRIPTION, check_connection
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

# Set by `python -m web` when the server listens only on this computer. Only then can the page change the
# Claude connection, and only then may runs use a Claude plan: Anthropic does not allow a claude.ai login
# to power a service for other people.
LOCAL = os.environ.get("VEDA_LOCAL") == "1"
ENV_FILE = ROOT / ".env"
# The most capable first. None has been tested on a real book through Claude yet.
API_MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "note": "API key, most capable"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "note": "API key, half the price of Opus"},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "note": "API key, cheapest"},
]
PLAN_MODELS = [
    {"id": SUBSCRIPTION + "claude-opus-5-5", "label": "Claude Opus 5.5", "note": "your Claude plan"},
    {"id": SUBSCRIPTION + "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "note": "your Claude plan, uses less of it"},
    {"id": SUBSCRIPTION + "claude-haiku-5-5", "label": "Claude Haiku 5.5", "note": "your Claude plan, uses the least"},
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
    model: str = Field(default=DEFAULT_MODEL, max_length=120)
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


def _plan_login() -> str | None:
    """How Claude Code signs in to the Claude plan here: a token saved on the page, the login of `claude`, or neither."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "token"
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return "login" if (config_dir / ".credentials.json").exists() else None


def _default_model() -> str:
    if LOCAL and _plan_login():
        return PLAN_MODELS[0]["id"]
    return DEFAULT_MODEL


def _models() -> list[dict]:
    """The models a run may use here, the default first."""
    plan = PLAN_MODELS if LOCAL else []
    return plan + API_MODELS if _default_model().startswith(SUBSCRIPTION) else API_MODELS + plan


def _connection() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return {"local": LOCAL, "plan": _plan_login() if LOCAL else None, "api_key": bool(key), "api_key_end": key[-4:] if key else ""}


def _explain(error: Exception) -> str:
    text = str(error)
    if "ANTHROPIC_API_KEY" in text:
        return "No Anthropic API key is saved. Add one under Claude connection, or pick a model that runs on your Claude plan."
    if "your Claude plan" in text:
        return text[:500]
    if isinstance(error, ModelHTTPError) and error.status_code == 401:
        return "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in the .env file."
    if isinstance(error, ModelHTTPError) and (error.status_code == 402 or "credit balance" in text.lower()):
        return "The Anthropic account is out of credits. Add credit at platform.claude.com."
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
    connection = _connection()
    return {"default_model": _default_model(), "models": _models(), "key_configured": connection["api_key"] or bool(connection["plan"]),
            "connection": connection, "samples": samples, "temporary_storage": TEMPORARY_STORAGE}


class ConnectionChange(BaseModel):
    """What the Claude connection form saves. A missing field is left as it is; an empty one is removed."""

    plan_token: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


def _local_only() -> None:
    if not LOCAL:
        raise HTTPException(403, "The Claude connection can only be changed on a copy running on your own computer.")


def _save_setting(name: str, value: str) -> None:
    """Keep the value in .env, which Git ignores, and use it from now on without a restart."""
    if value:
        ENV_FILE.touch(mode=0o600, exist_ok=True)
        set_key(ENV_FILE, name, value, quote_mode="never")
        os.environ[name] = value
    else:
        if ENV_FILE.exists():
            unset_key(ENV_FILE, name, quote_mode="never")
        os.environ.pop(name, None)


@app.get("/api/connection")
def connection() -> dict:
    return _connection()


@app.post("/api/connection")
def change_connection(body: ConnectionChange) -> dict:
    _local_only()
    token = (body.plan_token or "").strip()
    key = (body.api_key or "").strip()
    if body.plan_token is not None:
        if token.startswith("sk-ant-api"):
            raise HTTPException(422, "That is an API key, not a Claude plan token. Paste it in the API key box instead.")
        if token and (not token.startswith("sk-ant-") or any(c.isspace() for c in token)):
            raise HTTPException(422, "That does not look like a token from `claude setup-token`. It starts with sk-ant-oat.")
        _save_setting("CLAUDE_CODE_OAUTH_TOKEN", token)
    if body.api_key is not None:
        if key.startswith("sk-ant-oat"):
            raise HTTPException(422, "That is a Claude plan token, not an API key. Paste it in the Claude plan box instead.")
        if key and (not key.startswith("sk-ant-") or any(c.isspace() for c in key)):
            raise HTTPException(422, "That does not look like an Anthropic API key. It starts with sk-ant-api.")
        _save_setting("ANTHROPIC_API_KEY", key)
        api_model.cache_clear()  # models made with the old key
    return _connection()


class ConnectionTest(BaseModel):
    via: str = Field(pattern="^(plan|api)$")


@app.post("/api/connection/test")
async def test_connection(body: ConnectionTest) -> dict:
    """Make the smallest possible call, so the page can say whether the connection works before a run depends on it."""
    _local_only()
    if body.via == "plan":
        if not _plan_login():
            return {"ok": False, "message": "No Claude plan login on this computer yet. Paste a token from `claude setup-token`."}
        try:
            await check_connection()
        except RuntimeError as error:
            return {"ok": False, "message": str(error)[:500]}
        how = "the saved token" if _plan_login() == "token" else "this computer's Claude Code login"
        return {"ok": True, "message": f"Connected to your Claude plan through {how}."}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"ok": False, "message": "No API key is saved."}
    try:
        async with AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=1) as client:
            await client.models.retrieve(DEFAULT_MODEL)
    except AuthenticationError:
        return {"ok": False, "message": "Anthropic rejected this API key."}
    except Exception as error:
        return {"ok": False, "message": f"Could not reach Anthropic: {type(error).__name__}: {str(error)[:300]}"}
    return {"ok": True, "message": "The API key works."}


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
    if not any(m["id"] == body.model for m in _models()):
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
