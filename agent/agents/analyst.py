"""The Analyst agent: reads the book, writes its Spec, and tries the Spec on real pages before submitting."""

from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits
from pydantic_ai.usage import RunUsage

from .. import prompts
from ..core.spec import Spec, render_spec
from ..core.view import render_issues, render_pieces, render_records
from .deps import Workspace
from .labeler import ChunkDeps, run_labeler
from .models import ModelConfig, make_model, model_settings, price

MAX_TRIALS = 3
MAX_PAGES_PER_READ = 6
MAX_TRIAL_PAGES = 4


@dataclass
class AnalystDeps:
    ws: Workspace
    request: str
    examples: str
    models: ModelConfig
    duplicates: list[tuple[int, int, float]] = field(default_factory=list)
    draft: Spec | None = None
    trials: int = 0
    last_preview: str = ""
    last_preview_rows: list[dict] = field(default_factory=list)

    def log(self, tool: str, args: dict, result: str) -> str:
        self.ws.trace.log("analyst", "book", tool, args, result)
        return result


class AnalystResult(BaseModel):
    spec: Spec
    summary: str
    limitations: list[str]
    preview: str = ""
    preview_rows: list[dict] = []


async def submit(ctx: RunContext[AnalystDeps], summary: str, limitations: list[str]) -> AnalystResult:
    """Submit the current draft Spec as final.

    Args:
        summary: For the user, in plain language: what each row holds, what goes in each column, where headings go, what is removed, how rows are numbered.
        limitations: Things the user asked for that this book cannot provide, and oddities they should know about. Empty if none.
    """
    deps = ctx.deps
    if deps.draft is None:
        raise ModelRetry("There is no draft yet. Call draft_spec first.")
    if deps.trials == 0:
        raise ModelRetry("The draft has not been tried on real pages. Call trial_run at least once before submitting.")
    deps.log("submit", {"summary": summary, "limitations": limitations}, "ok")
    return AnalystResult(spec=deps.draft, summary=summary, limitations=limitations, preview=deps.last_preview, preview_rows=deps.last_preview_rows)


analyst = Agent(
    output_type=ToolOutput(submit, name="submit", max_retries=4),
    deps_type=AnalystDeps,
    instructions=prompts.load("domain", "rules", "analyst"),
    retries=4,
)


@analyst.tool
async def overview(ctx: RunContext[AnalystDeps]) -> str:
    """Facts about the whole book: size, how verse numbers run, lines that look like section closers, page furniture counts, duplicated pages."""
    deps = ctx.deps
    pieces = deps.ws.pieces
    hints = Counter(h for p in pieces for h in p.hints)
    numbers = ["-".join(map(str, (p.printed_n[0], p.printed_n[-1]))) if len(p.printed_n) > 1 else str(p.printed_n[0]) for p in pieces if p.printed_n]
    closers = [p for p in pieces if p.text.lstrip("#• ").startswith("इति ") and len(p.text) < 90]
    lines = [
        f"Pages: {pieces[-1].page}. Pieces: {len(pieces)} (IDs {pieces[0].id}–{pieces[-1].id}).",
        "Hints: " + ", ".join(f"{name} ×{count}" for name, count in hints.most_common()),
        f"Printed verse numbers in reading order ({len(numbers)}): " + " ".join(numbers[:150]) + (" …" if len(numbers) > 150 else ""),
        f"Short lines starting with इति ({len(closers)}):",
        *[f"  {p.id} (page {p.page}) | {p.text}" for p in closers[:40]],
    ]
    if deps.duplicates:
        lines.append("Pages that repeat an earlier page (OCR duplicates): " + ", ".join(f"page {b} repeats page {a}" for a, b, _ in deps.duplicates))
    return deps.log("overview", {}, "\n".join(lines))


@analyst.tool
async def read_pages(ctx: RunContext[AnalystDeps], first_page: int, last_page: int) -> str:
    """Read pages of the book as pieces with their IDs and hints. At most 6 pages per call.

    Args:
        first_page: First page to read (pages start at 1).
        last_page: Last page to read.
    """
    deps = ctx.deps
    last_page = min(last_page, first_page + MAX_PAGES_PER_READ - 1)
    found = deps.ws.page(first_page, last_page)
    deps.log("read_pages", {"first_page": first_page, "last_page": last_page}, f"{len(found)} pieces")
    return render_pieces(found) if found else f"No pages in that range. The book has pages 1–{deps.ws.pieces[-1].page}."


@analyst.tool
async def search(ctx: RunContext[AnalystDeps], text: str) -> str:
    """Find every piece that contains the given text. Use it to see all section titles, closing lines, or any repeated pattern.

    Args:
        text: Text to look for, e.g. 'ब्राह्मणम्' or 'इति'.
    """
    deps = ctx.deps
    hits = [p for p in deps.ws.pieces if text in p.text]
    shown = "\n".join(f"{p.id} (page {p.page}) | {p.text[:100]}" for p in hits[:50])
    result = f"{len(hits)} piece(s) contain '{text}'" + (" (first 50 shown)" if len(hits) > 50 else "") + (":\n" + shown if hits else ".")
    return deps.log("search", {"text": text}, result)


@analyst.tool
async def draft_spec(ctx: RunContext[AnalystDeps], spec: Spec) -> str:
    """Save a draft Spec, replacing any earlier draft. Problems in the Spec are reported so you can fix them.

    Args:
        spec: The whole Spec.
    """
    deps = ctx.deps
    deps.draft = spec
    return deps.log("draft_spec", {"spec": spec.model_dump()}, f"Draft saved: {len(spec.fields)} fields, {len(spec.roles)} roles, levels {spec.levels or 'none'}.")


@analyst.tool
async def trial_run(ctx: RunContext[AnalystDeps], first_page: int, last_page: int) -> str:
    """Run a Labeler on a few pages with the current draft and see the rows and issues it produces. At most 4 pages and 3 trial runs.

    Args:
        first_page: First page of the trial.
        last_page: Last page of the trial.
    """
    deps = ctx.deps
    if deps.draft is None:
        return "There is no draft yet. Call draft_spec first."
    if deps.trials >= MAX_TRIALS:
        return "No trial runs left. Submit the Spec, adjusting the draft first if the last trial showed a problem."
    last_page = min(last_page, first_page + MAX_TRIAL_PAGES - 1)
    deps.trials += 1
    scratch = deps.ws.scratch_copy()
    chunk = ChunkDeps(ws=scratch, spec=deps.draft, first_page=first_page, last_page=last_page, scope=f"trial {deps.trials}")
    outcome = await run_labeler(chunk, deps.models.labeler, deps.models.effort)
    built = chunk.check()
    deps.last_preview = render_records(built.records, limit=400, local_name=deps.draft.unit_name)
    deps.last_preview_rows = [{"label": f"{deps.draft.unit_name} {r.n}", **r.fields} for r in built.records]
    result = "\n\n".join([
        f"Trial {deps.trials} of {MAX_TRIALS} on pages {first_page}–{last_page}. Labeler said: {outcome.summary}",
        "Rows (rows at the edges of these pages may be incomplete):\n" + render_records(built.records, limit=260, local_name=deps.draft.unit_name),
        "Checks:\n" + render_issues(built.issues),
        "Labels the Labeler chose:\n" + render_pieces(chunk.pieces(), scratch.store),
    ])
    return deps.log("trial_run", {"first_page": first_page, "last_page": last_page}, result)


def analyst_prompt(deps: AnalystDeps, feedback: str = "", previous: Spec | None = None) -> str:
    parts = ["## User request\n\n" + deps.request.strip()]
    parts.append("## Example rows from the user\n\n" + (deps.examples.strip() or "(none given)"))
    if previous is not None:
        parts.append("## Your previous Spec\n\n```json\n" + previous.model_dump_json(indent=1) + "\n```")
        parts.append("## The user's feedback on it\n\n" + feedback.strip() + "\n\nRevise the Spec to address this feedback.")
    parts.append("Start with `overview`.")
    return "\n\n".join(parts)


async def run_analyst(deps: AnalystDeps, feedback: str = "", previous: Spec | None = None, request_limit: int = 40) -> AnalystResult:
    """Run the Analyst. Raises AgentRunError if it cannot produce a Spec."""
    usage = RunUsage()
    try:
        result = await analyst.run(
            analyst_prompt(deps, feedback, previous), deps=deps, model=make_model(deps.models.analyst),
            model_settings=model_settings(deps.models.effort), usage_limits=UsageLimits(request_limit=request_limit), usage=usage,
        )
    finally:
        deps.ws.usage.add("analyst", deps.models.analyst, usage, price(deps.models.analyst))
    return result.output


def describe(result: AnalystResult) -> str:
    """What the user is shown before approving."""
    parts = ["WHAT THE OUTPUT WILL LOOK LIKE", "", result.summary.strip(), ""]
    if result.limitations:
        parts += ["LIMITATIONS", ""] + [f"- {item}" for item in result.limitations] + [""]
    parts += ["SAMPLE ROWS FROM A TRIAL (rows at the edges of the sample may be incomplete)", "", result.preview or "(none)", "", "EXTRACTION RULES (the Spec)", "", render_spec(result.spec)]
    return "\n".join(parts)
