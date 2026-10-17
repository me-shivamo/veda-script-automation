"""Runs one job from OCR text to output files.

Code owns the control flow, so no chunk is skipped and a failed step is always
recorded. The three agents do the judgement inside bounded loops.
"""

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

from .agents.analyst import AnalystDeps, AnalystResult, run_analyst, view_of
from .agents.deps import Trace, Workspace
from .agents.labeler import ChunkDeps, run_labeler
from .agents.models import ModelConfig
from .agents.reviewer import ReviewDeps, run_reviewer
from .core.duplicates import find_duplicate_pages
from .core.fidelity import check_fidelity
from .core.labels import LabelStore
from .core.pieces import make_pieces, page_numbers
from .core.spec import Spec, StructureEdit, apply_structure_edits
from .export import write_all
from .jobs import Job
from .plan import describe_plan

# An approval callback gets the Analyst's result and the plan screen's data. It returns True to accept, False to
# stop, text to send back to the Analyst, or a dict {"feedback": text, "answers": {question: answer}, "edits": [...]}:
# edits to the output structure are made by code, and feedback or answers go to the Analyst.
Approve = Callable[[AnalystResult, dict], Awaitable[bool | str | dict]]
MAX_PLAN_VERSIONS = 20


@dataclass
class RunConfig:
    models: ModelConfig = field(default_factory=ModelConfig)
    chunk_pages: int = 6
    concurrency: int = 6
    auto_approve: bool = False
    labeler_requests: int = 30
    max_revisions: int = 3
    pages: tuple[int, int] | None = None  # only process this page range (for trials)


def _chunks(pages: list[int], size: int) -> list[tuple[int, int]]:
    return [(pages[i], pages[min(i + size, len(pages)) - 1]) for i in range(0, len(pages), size)]


def _feedback_text(answer: dict) -> str:
    parts = [str(answer.get("feedback") or "").strip()]
    answers = {q: a for q, a in (answer.get("answers") or {}).items() if str(a).strip()}
    if answers:
        parts.append("Answers to your questions:\n" + "\n".join(f"- {q}\n  Answer: {a}" for q, a in answers.items()))
    return "\n\n".join(p for p in parts if p)


async def _get_spec(job: Job, ws: Workspace, config: RunConfig, duplicates, approve: Approve | None, say, on_event) -> Spec | None:
    if job.has("spec.json"):
        say("Using the Spec already saved in this job.")
        return Spec.model_validate(job.load_json("spec.json"))
    on_event({"type": "phase", "phase": "analysing"})

    def deps() -> AnalystDeps:
        return AnalystDeps(ws=ws, request=job.read("request.txt"), examples=job.read("examples.csv"), models=config.models, duplicates=duplicates)

    say(f"Analyst ({config.models.analyst}) is reading the book and writing the Spec…")
    result = await run_analyst(deps())
    version, revisions, previous, note = 1, 0, None, ""
    while True:
        view = view_of(result, ws.pieces, version=version, previous=previous, note=note)
        job.save_json(f"plan_{version}.json", view)
        job.write("analyst.md", describe_plan(view))
        if config.auto_approve or approve is None:
            break
        answer = await approve(result, view)
        if answer is True:
            break
        if answer is False:
            return None
        answer = answer if isinstance(answer, dict) else {"feedback": answer}
        previous, note = result.spec, ""
        spec = result.spec
        try:
            spec = apply_structure_edits(spec, [StructureEdit.model_validate(e) for e in answer.get("edits") or []])
        except ValueError as error:
            note = f"That change could not be made: {error}"
        text = _feedback_text(answer)
        if text and revisions < config.max_revisions:
            revisions += 1
            say("Analyst is revising the plan with your feedback…")
            on_event({"type": "phase", "phase": "analysing"})
            result = await run_analyst(deps(), feedback=text, previous=spec)
        else:
            if text:
                note = f"The Analyst has already revised this plan {revisions} times, so only the structure edits were made. " + note
            say("The output structure was changed as you asked.")
            result = result.model_copy(update={"spec": spec})
        version += 1
        if version > MAX_PLAN_VERSIONS:
            say(f"Stopping: {MAX_PLAN_VERSIONS} versions of the plan without approval.")
            return None
    job.save_json("spec.json", result.spec.model_dump())
    job.save_json("plan.json", view)
    return result.spec


async def run_job(job: Job, config: RunConfig, approve: Approve | None = None, say: Callable[[str], None] = print,
                  on_event: Callable[[dict], None] = lambda event: None) -> dict:
    """Run one job. `say` receives progress lines for people; `on_event` receives the same progress as data."""
    started = time.time()
    pieces = make_pieces(job.read("input.md"))
    if config.pages:
        pieces = [p for p in pieces if config.pages[0] <= p.page <= config.pages[1]]
    ws = Workspace(pieces=pieces, store=LabelStore(), trace=Trace(job.path("trace.jsonl")))
    duplicates = find_duplicate_pages(pieces)
    say(f"Book: {len(page_numbers(pieces))} pages, {len(pieces)} pieces." + (f" {len(duplicates)} duplicated page(s) found." if duplicates else ""))

    on_event({"type": "book", "pages": len(page_numbers(pieces)), "pieces": len(pieces), "duplicate_pages": len(duplicates)})
    spec = await _get_spec(job, ws, config, duplicates, approve, say, on_event)
    if spec is None:
        job.save_json("state.json", {"state": "STOPPED", "reason": "the Spec was not approved"})
        return {"state": "STOPPED"}

    # Label every chunk, several at a time.
    chunks = _chunks(page_numbers(pieces), config.chunk_pages)
    limit = asyncio.Semaphore(config.concurrency)
    duplicate_note = ("Note: " + "; ".join(f"page {b} repeats page {a}" for a, b, _ in duplicates) + " (OCR duplicates). Flag duplicated pages.") if duplicates else ""
    outcomes: list[dict] = []

    async def label_chunk(number: int, first: int, last: int) -> None:
        async with limit:
            deps = ChunkDeps(ws=ws, spec=spec, first_page=first, last_page=last, scope=f"chunk {number} (pages {first}–{last})", notes=duplicate_note)
            outcome = await run_labeler(deps, config.models.labeler, config.models.effort, config.labeler_requests)
            model_used = config.models.labeler
            stronger = config.models.escalation
            if not outcome.ok and stronger and stronger != config.models.labeler:
                say(f"  chunk {number}: not finished by {config.models.labeler} ({outcome.summary[:80]}); retrying on {stronger}")
                for p in deps.pieces():
                    ws.store.labels.pop(p.id, None)
                    ws.store.flags.pop(p.id, None)
                outcome = await run_labeler(deps, stronger, config.models.effort, config.labeler_requests)
                model_used = stronger
            if not outcome.ok:
                for issue in deps.check().errors:
                    ws.store.flag(issue.pieces, f"labeler could not resolve: {issue.kind}")
            outcomes.append({"chunk": number, "pages": [first, last], "ok": outcome.ok, "model": model_used, "requests": outcome.requests, "summary": outcome.summary})
            say(f"  chunk {number}/{len(chunks)} (pages {first}–{last}): {'ok' if outcome.ok else 'NOT FINISHED'} in {outcome.requests} steps")
            on_event({"type": "chunk", "done": len(outcomes), "total": len(chunks), "ok": outcome.ok})

    say(f"Labeler ({config.models.labeler}) is labelling {len(chunks)} chunk(s), up to {config.concurrency} at a time…")
    on_event({"type": "phase", "phase": "labelling", "total": len(chunks)})
    await asyncio.gather(*(label_chunk(i + 1, a, b) for i, (a, b) in enumerate(chunks)))
    job.save_json("labels.json", ws.store.model_dump())

    # Stitch the book together, then let the Reviewer deal with what code finds.
    built = ws.build(spec)
    review_summary = ""
    if built.errors or ws.store.flags:
        say(f"Code found {len(built.errors)} error(s) across the book and Labelers flagged {len(ws.store.flags)} piece(s). "
            f"Reviewer ({config.models.reviewer}) is working on them…")
        on_event({"type": "phase", "phase": "reviewing"})
        review_summary = await run_reviewer(ReviewDeps(ws=ws, spec=spec), config.models.reviewer, config.models.effort)
        built = ws.build(spec)
        job.save_json("labels.json", ws.store.model_dump())

    on_event({"type": "phase", "phase": "assembling"})
    # Independent proof that every output character is the book's own, before anything is written.
    not_verbatim = check_fidelity(job.read("input.md"), ws.pieces, built.records + built.sections, built.dropped, config.pages)
    if not_verbatim:
        say(f"WARNING: {len(not_verbatim)} output field(s) are not an exact copy of the book text. See the review list.")
    order = {p.id: i for i, p in enumerate(ws.pieces)}
    files = write_all(built.records, spec, job.dir, built.sections, order)
    job.save_json("pieces.json", [p.model_dump() for p in ws.pieces])
    by_id = {p.id: p for p in ws.pieces}
    job.save_json("removed.json", [{"piece": pid, "page": by_id[pid].page, "role": ws.store.labels[pid].role, "text": by_id[pid].text}
                                   for pid in built.dropped])
    errors = not_verbatim + built.errors
    review_list = [i.model_dump() for i in not_verbatim + built.issues]
    # A flag on a piece that an issue already names is shown in that issue; the rest are grouped by reason.
    named = {pid for i in built.issues for pid in i.pieces}
    by_reason: dict[str, list[str]] = {}
    for pid, reason in ws.store.flags.items():
        if pid not in named:
            by_reason.setdefault(reason, []).append(pid)
    review_list += [{"kind": "flagged", "severity": "warning", "message": reason, "pieces": pids, "unit": None} for reason, pids in by_reason.items()]
    review_list += [{"kind": "duplicate_page", "severity": "warning", "message": f"page {b} repeats page {a} (overlap {s})", "pieces": [], "unit": None} for a, b, s in duplicates]
    job.save_json("issues.json", review_list)

    state = "NEEDS_HUMAN" if errors else "DONE"
    summary = {
        "state": state,
        "rows": len(built.records),
        "sections": len(built.sections),
        "shape": spec.output.shape,
        "files": files,
        "errors": len(errors),
        "warnings": len(review_list) - len(errors),
        "flagged_pieces": len(ws.store.flags),
        "dropped_pieces": len(built.dropped),
        "verbatim": not not_verbatim,
        "seconds": round(time.time() - started, 1),
        "cost_usd": round(ws.usage.cost, 4),
        "requests": ws.usage.requests,
        "input_tokens": ws.usage.input_tokens,
        "output_tokens": ws.usage.output_tokens,
        "models": asdict(config.models),
        "usage_by_agent": ws.usage.by_agent,
        "chunks": sorted(outcomes, key=lambda o: o["chunk"]),
        "review_summary": review_summary,
    }
    job.save_json("state.json", summary)
    say(f"{state}: {summary['rows']} rows, {summary['errors']} errors, {summary['warnings']} warnings, {summary['flagged_pieces']} flagged pieces. "
        f"{summary['seconds']}s, ${summary['cost_usd']}. Output: {job.path('output.csv')}")
    return summary
