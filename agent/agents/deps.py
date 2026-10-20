"""Shared state that agent tools work on."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.assemble import BuildResult, build
from ..core.labels import LabelStore
from ..core.pieces import Piece
from ..core.spec import Spec


@dataclass
class Trace:
    """Appends one JSON line per tool call, so a run can be read back afterwards."""

    path: Path | None = None

    def log(self, agent: str, scope: str, tool: str, args: dict, result: str) -> None:
        if self.path is None:
            return
        row = {"t": round(time.time(), 2), "agent": agent, "scope": scope, "tool": tool, "args": args, "result": result[:2000]}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    by_agent: dict = field(default_factory=dict)

    def add(self, agent: str, model_id: str, run_usage, price: tuple[float, float] | None = None) -> None:
        cost = 0.0
        if price:
            # input_tokens includes the cached tokens: a cache write costs 1.25 times the input price, a read 0.1 times.
            written, read = run_usage.cache_write_tokens, run_usage.cache_read_tokens
            fresh = run_usage.input_tokens - written - read
            cost = ((fresh + 1.25 * written + 0.1 * read) * price[0] + run_usage.output_tokens * price[1]) / 1_000_000
        self.requests += run_usage.requests
        self.input_tokens += run_usage.input_tokens
        self.output_tokens += run_usage.output_tokens
        self.cost += cost
        row = self.by_agent.setdefault(f"{agent} ({model_id})", {"runs": 0, "requests": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "cost": 0.0})
        row["runs"] += 1
        row["requests"] += run_usage.requests
        row["input_tokens"] += run_usage.input_tokens
        row["cache_read_tokens"] += run_usage.cache_read_tokens
        row["output_tokens"] += run_usage.output_tokens
        row["cost"] = round(row["cost"] + cost, 6)


@dataclass
class Workspace:
    """The mutable state of one job: the pieces and the labels on them."""

    pieces: list[Piece]
    store: LabelStore
    trace: Trace = field(default_factory=Trace)
    usage: Usage = field(default_factory=Usage)

    def scratch_copy(self) -> "Workspace":
        """A copy for trial runs, so they cannot touch the real labels or pieces."""
        return Workspace(pieces=[p.model_copy() for p in self.pieces], store=LabelStore(), trace=self.trace, usage=self.usage)

    def page(self, first: int, last: int) -> list[Piece]:
        return [p for p in self.pieces if first <= p.page <= last]

    def build(self, spec: Spec) -> BuildResult:
        return build(self.pieces, self.store, spec, scope="book")
