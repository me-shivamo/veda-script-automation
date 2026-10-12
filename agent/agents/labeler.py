"""The Labeler agent: labels every piece in one chunk, checks itself, and loops until the check passes."""

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.usage import RunUsage

from .. import prompts
from ..core.assemble import BuildResult, build
from ..core.labels import Span
from ..core.pieces import Piece, split_piece as split_piece_in
from ..core.spec import Spec, render_spec
from ..core.view import render_issues, render_pieces, render_records
from .deps import Workspace
from .models import make_model, model_settings, price

CONTEXT_PIECES = 25


@dataclass
class ChunkDeps:
    ws: Workspace
    spec: Spec
    first_page: int
    last_page: int
    scope: str = "chunk"
    notes: str = ""

    def pieces(self) -> list[Piece]:
        return self.ws.page(self.first_page, self.last_page)

    def check(self) -> BuildResult:
        return build(self.pieces(), self.ws.store, self.spec, scope="chunk")

    def log(self, tool: str, args: dict, result: str) -> str:
        self.ws.trace.log("labeler", self.scope, tool, args, result)
        return result


class ChunkResult(BaseModel):
    summary: str


async def finish(ctx: RunContext[ChunkDeps], summary: str) -> ChunkResult:
    """Finish this chunk. Refused while unflagged errors remain.

    Args:
        summary: One or two sentences on what the chunk contained and anything you flagged.
    """
    errors = ctx.deps.check().errors
    if errors:
        message = "Cannot finish yet.\n" + render_issues(errors) + "\nFix these with `label`, or `flag` the pieces if you cannot resolve them."
        ctx.deps.log("finish", {"summary": summary}, "REFUSED: " + message)
        raise ModelRetry(message)
    ctx.deps.log("finish", {"summary": summary}, "ok")
    return ChunkResult(summary=summary)


labeler = Agent(
    output_type=ToolOutput(finish, name="finish", max_retries=4),
    deps_type=ChunkDeps,
    instructions=prompts.load("domain", "rules", "labeler"),
    retries=3,
)


@labeler.instructions
def _spec_for_this_book(ctx: RunContext[ChunkDeps]) -> str:
    return "# The Spec for this book\n\n" + render_spec(ctx.deps.spec)


@labeler.tool
async def label(ctx: RunContext[ChunkDeps], spans: list[Span]) -> str:
    """Label runs of consecutive pieces. Later calls overwrite earlier labels on the same pieces.

    Args:
        spans: Runs of pieces in reading order. Together they should cover every piece in your chunk.
    """
    deps = ctx.deps
    allowed = {p.id for p in deps.pieces()}
    done, errors = deps.ws.store.apply(spans, deps.ws.pieces, deps.spec, allowed=allowed)
    missing = [p.id for p in deps.pieces() if p.id not in deps.ws.store.labels]
    message = f"Labelled {done} pieces."
    if errors:
        message += "\nRejected:\n" + "\n".join("- " + e for e in errors)
    message += "\nEvery piece in the chunk has a label." if not missing else f"\nStill unlabelled ({len(missing)}): {', '.join(missing[:40])}"
    return deps.log("label", {"spans": [s.model_dump() for s in spans]}, message)


@labeler.tool
async def split_piece(ctx: RunContext[ChunkDeps], piece_id: str, after_text: str) -> str:
    """Split one piece in two when it holds two different things, e.g. a side heading glued to a sentence.

    Args:
        piece_id: The piece to split.
        after_text: Text copied exactly from the piece. The cut is made right after it. It must occur exactly once in the piece.
    """
    deps = ctx.deps
    if piece_id not in {p.id for p in deps.pieces()}:
        return deps.log("split_piece", {"piece_id": piece_id}, f"{piece_id} is not a piece in your chunk.")
    try:
        a, b = split_piece_in(deps.ws.pieces, piece_id, after_text)
    except ValueError as e:
        return deps.log("split_piece", {"piece_id": piece_id, "after_text": after_text}, f"Not split: {e}")
    deps.ws.store.rename_split(piece_id, (a.id, b.id))
    return deps.log("split_piece", {"piece_id": piece_id, "after_text": after_text},
                    f"Split into {a.id} | {a.text}  and  {b.id} | {b.text}. Label both.")


@labeler.tool
async def check(ctx: RunContext[ChunkDeps]) -> str:
    """Check the labels on your chunk: coverage, numbering order, missing parts, text that was dropped."""
    return ctx.deps.log("check", {}, render_issues(ctx.deps.check().issues))


@labeler.tool
async def preview(ctx: RunContext[ChunkDeps]) -> str:
    """Show the rows your labels produce for this chunk (long text is shortened). Rows at the edges of the chunk may be incomplete."""
    return ctx.deps.log("preview", {}, render_records(ctx.deps.check().records, local_name=ctx.deps.spec.unit_name))


@labeler.tool
async def flag(ctx: RunContext[ChunkDeps], piece_ids: list[str], reason: str) -> str:
    """Hand pieces you cannot decide to a person. Still give each one your best label first.

    Args:
        piece_ids: The pieces in doubt.
        reason: What a person needs to know to decide, in one sentence.
    """
    deps = ctx.deps
    known = {p.id for p in deps.pieces()}
    ids = [i for i in piece_ids if i in known]
    deps.ws.store.flag(ids, reason)
    return deps.log("flag", {"piece_ids": piece_ids, "reason": reason}, f"Flagged {len(ids)} piece(s) for a person to review.")


def chunk_prompt(deps: ChunkDeps) -> str:
    before = deps.ws.page(deps.first_page - 1, deps.first_page - 1)[-CONTEXT_PIECES:]
    after = deps.ws.page(deps.last_page + 1, deps.last_page + 1)[:CONTEXT_PIECES]
    parts = [f"Label every piece on pages {deps.first_page}–{deps.last_page}."]
    if deps.notes:
        parts.append(deps.notes)
    parts.append("## Context before (do not label)\n\n" + (render_pieces(before) if before else "(this chunk starts the book)"))
    parts.append("## Pieces to label\n\n" + render_pieces(deps.pieces()))
    parts.append("## Context after (do not label)\n\n" + (render_pieces(after) if after else "(this chunk ends the book)"))
    return "\n\n".join(parts)


@dataclass
class ChunkOutcome:
    ok: bool
    summary: str
    errors: int
    requests: int


async def run_labeler(deps: ChunkDeps, model_id: str, effort: str, request_limit: int = 30) -> ChunkOutcome:
    """Run the Labeler on one chunk. Never raises for model failures; reports them in the outcome."""
    usage = RunUsage()
    try:
        result = await labeler.run(
            chunk_prompt(deps), deps=deps, model=make_model(model_id), model_settings=model_settings(effort),
            usage_limits=UsageLimits(request_limit=request_limit), usage=usage,
        )
        summary = result.output.summary
    except AgentRunError as e:
        summary = f"did not finish: {type(e).__name__}: {e}"
    deps.ws.usage.add("labeler", model_id, usage, price(model_id))
    errors = len(deps.check().errors)
    ok = errors == 0 and not summary.startswith("did not finish")
    deps.ws.trace.log("labeler", deps.scope, "run_end", {"model": model_id, "requests": usage.requests}, summary)
    return ChunkOutcome(ok=ok, summary=summary, errors=errors, requests=usage.requests)
