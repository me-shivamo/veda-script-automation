"""The Reviewer agent: resolves the problems code found in the assembled book."""

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage

from .. import prompts
from ..core.labels import Span
from ..core.pieces import split_piece as split_piece_in
from ..core.spec import Spec, render_spec
from ..core.view import render_issues, render_pieces, render_records
from .deps import Workspace
from .models import make_model, model_settings, price


@dataclass
class ReviewDeps:
    ws: Workspace
    spec: Spec

    def log(self, tool: str, args: dict, result: str) -> str:
        self.ws.trace.log("reviewer", "book", tool, args, result)
        return result


class ReviewResult(BaseModel):
    summary: str


async def finish(ctx: RunContext[ReviewDeps], summary: str) -> ReviewResult:
    """Finish the review. Refused while unflagged errors remain.

    Args:
        summary: What you fixed and what you flagged for a person, in a few sentences.
    """
    errors = ctx.deps.ws.build(ctx.deps.spec).errors
    if errors:
        message = "Cannot finish yet.\n" + render_issues(errors) + "\nFix these with `relabel`, or `flag` the pieces with a reason."
        ctx.deps.log("finish", {"summary": summary}, "REFUSED: " + message)
        raise ModelRetry(message)
    ctx.deps.log("finish", {"summary": summary}, "ok")
    return ReviewResult(summary=summary)


reviewer = Agent(
    output_type=ToolOutput(finish, name="finish", max_retries=4),
    deps_type=ReviewDeps,
    instructions=prompts.load("domain", "rules", "reviewer"),
    retries=3,
)


@reviewer.instructions
def _spec_for_this_book(ctx: RunContext[ReviewDeps]) -> str:
    return "# The Spec for this book\n\n" + render_spec(ctx.deps.spec)


def _issue_list(deps: ReviewDeps) -> str:
    issues = deps.ws.build(deps.spec).issues
    rows = [f"{i.severity.upper()} {i.kind} [{', '.join(i.pieces) or i.unit or '-'}]: {i.message}" for i in issues[:80]]
    more = [f"… and {len(issues) - 80} more"] if len(issues) > 80 else []
    flags: dict[str, list[str]] = {}
    for piece_id, reason in deps.ws.store.flags.items():
        flags.setdefault(reason, []).append(piece_id)
    flagged = [f"FLAGGED [{', '.join(ids)}]: {reason}" for reason, ids in flags.items()]
    head = render_issues(issues).split("\n")[0] + f" {len(deps.ws.store.flags)} flagged piece(s)."
    return "\n".join([head, *rows, *more, *flagged])


@reviewer.tool
async def list_issues(ctx: RunContext[ReviewDeps]) -> str:
    """List the problems code found in the whole book, with the pieces involved."""
    return ctx.deps.log("list_issues", {}, _issue_list(ctx.deps))


@reviewer.tool
async def check_book(ctx: RunContext[ReviewDeps]) -> str:
    """Re-run the checks on the whole book after your changes."""
    return ctx.deps.log("check_book", {}, _issue_list(ctx.deps))


@reviewer.tool
async def view(ctx: RunContext[ReviewDeps], piece_id: str, radius: int = 10) -> str:
    """Show a piece with the pieces around it and their current labels.

    Args:
        piece_id: The piece to look at.
        radius: How many pieces to show on each side (at most 40).
    """
    deps = ctx.deps
    index = next((i for i, p in enumerate(deps.ws.pieces) if p.id == piece_id), None)
    if index is None:
        return f"{piece_id} does not exist."
    radius = max(1, min(radius, 40))
    window = deps.ws.pieces[max(index - radius, 0):index + radius + 1]
    deps.log("view", {"piece_id": piece_id, "radius": radius}, f"{len(window)} pieces")
    return render_pieces(window, deps.ws.store)


@reviewer.tool
async def view_record(ctx: RunContext[ReviewDeps], record_id: str) -> str:
    """Show one assembled row in full, with the pieces each column was built from.

    Args:
        record_id: The row ID, e.g. '1.2.5'.
    """
    deps = ctx.deps
    record = next((r for r in deps.ws.build(deps.spec).records if r.id == record_id), None)
    if record is None:
        return f"There is no row {record_id}."
    sources = "\n".join(f"  {name} ← {', '.join(ids)}" for name, ids in record.sources.items())
    return deps.log("view_record", {"record_id": record_id}, render_records([record], limit=1200) + "\nBuilt from:\n" + sources)


@reviewer.tool
async def relabel(ctx: RunContext[ReviewDeps], spans: list[Span]) -> str:
    """Change the labels on runs of pieces.

    Args:
        spans: The runs to relabel. Only the pieces you name are changed.
    """
    deps = ctx.deps
    done, errors = deps.ws.store.apply(spans, deps.ws.pieces, deps.spec)
    message = f"Relabelled {done} pieces." + ("\nRejected:\n" + "\n".join("- " + e for e in errors) if errors else "")
    return deps.log("relabel", {"spans": [s.model_dump() for s in spans]}, message)


@reviewer.tool
async def split_piece(ctx: RunContext[ReviewDeps], piece_id: str, after_text: str) -> str:
    """Split one piece in two when it holds two different things.

    Args:
        piece_id: The piece to split.
        after_text: Text copied exactly from the piece. The cut is made right after it. It must occur exactly once in the piece.
    """
    deps = ctx.deps
    try:
        a, b = split_piece_in(deps.ws.pieces, piece_id, after_text)
    except ValueError as e:
        return deps.log("split_piece", {"piece_id": piece_id, "after_text": after_text}, f"Not split: {e}")
    deps.ws.store.rename_split(piece_id, (a.id, b.id))
    return deps.log("split_piece", {"piece_id": piece_id, "after_text": after_text}, f"Split into {a.id} | {a.text}  and  {b.id} | {b.text}. Both keep the old label until you relabel them.")


@reviewer.tool
async def flag(ctx: RunContext[ReviewDeps], piece_ids: list[str], reason: str) -> str:
    """Hand pieces to a person when the problem is in the book or the OCR, not in the labels.

    Args:
        piece_ids: The pieces involved.
        reason: What a person needs to know to decide, in one sentence.
    """
    deps = ctx.deps
    known = {p.id for p in deps.ws.pieces}
    ids = [i for i in piece_ids if i in known]
    deps.ws.store.flag(ids, reason)
    return deps.log("flag", {"piece_ids": piece_ids, "reason": reason}, f"Flagged {len(ids)} piece(s) for a person to review.")


@reviewer.tool
async def clear_flag(ctx: RunContext[ReviewDeps], piece_ids: list[str]) -> str:
    """Remove the flag from pieces whose problem you have resolved. If a check still fails on them, the error comes back.

    Args:
        piece_ids: The pieces to unflag.
    """
    deps = ctx.deps
    cleared = [i for i in piece_ids if deps.ws.store.flags.pop(i, None) is not None]
    return deps.log("clear_flag", {"piece_ids": piece_ids}, f"Cleared the flag on {len(cleared)} piece(s).")


async def run_reviewer(deps: ReviewDeps, model_id: str, effort: str, request_limit: int = 40) -> str:
    """Run the Reviewer. Never raises for model failures; returns its summary or the reason it stopped."""
    usage = RunUsage()
    try:
        result = await reviewer.run(
            "Code has assembled the book and found problems. Start with `list_issues`.", deps=deps, model=make_model(model_id),
            model_settings=model_settings(effort), usage_limits=UsageLimits(request_limit=request_limit), usage=usage,
        )
        summary = result.output.summary
    except AgentRunError as e:
        summary = f"did not finish: {type(e).__name__}: {e}"
    deps.ws.usage.add("reviewer", model_id, usage, price(model_id))
    deps.ws.trace.log("reviewer", "book", "run_end", {"model": model_id, "requests": usage.requests}, summary)
    return summary
