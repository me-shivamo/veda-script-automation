"""Labels that agents put on pieces."""

from pydantic import BaseModel, Field

from .pieces import Piece
from .spec import Spec


class Label(BaseModel):
    role: str
    n: list[int] = []
    at: list[int] = []


class Span(BaseModel):
    """A run of consecutive pieces that share one label."""

    start: str = Field(description="ID of the first piece in the run, e.g. 'P0012'.")
    end: str = Field(description="ID of the last piece in the run (same as start for a single piece).")
    role: str = Field(description="A role name from the Spec.")
    n: list[int] = Field(default_factory=list, description=(
        "For 'unit' roles: the number of the unit this text belongs to, as it should be, e.g. [5]. "
        "Give several, e.g. [12, 13], only when one passage belongs to several units. Leave empty for other kinds. "
        "For a locator, optionally the unit numbers it prints, e.g. [1, 3] for 'मं. १-३'."
    ))
    at: list[int] = Field(default_factory=list, description=(
        "The section path, one number per section level, outermost first, e.g. [1, 4] for adhyaya 1, khanda 4. "
        "Required on locator lines (the path they print). Required on numbered spans when the Spec's position is 'printed'; "
        "optional on attach spans there. Use 0 for an optional level that this section does not have. Leave empty otherwise."
    ))


def _check_at(s: Span, kind: str, spec: Spec) -> str | None:
    """Why the span's 'at' is unusable, or None."""
    levels = spec.levels
    example = "[" + ", ".join(str(i + 1) for i in range(len(levels))) + "]"
    if kind == "locator":
        if not levels:
            return "this book has no section levels, so a locator has nothing to point at; use a drop role"
        if not s.at or len(s.at) > len(levels) or any(k < 0 for k in s.at):
            return f"a locator needs 'at', the section path it prints, e.g. {example} ({', '.join(spec.level_names)})"
        return None
    if spec.position != "printed" or not s.at and kind != "unit":
        return None
    if len(s.at) != len(levels):
        return (f"position is 'printed', so this span needs 'at' with one number per level ({', '.join(spec.level_names)}), "
                f"e.g. {example}, read from the page's locator line")
    for k, lv in zip(s.at, levels):
        if k < 0 or (k == 0 and not lv.optional):
            return f"'at' has {k} for {lv.name}; only an optional level may be 0"
    return None


class LabelStore(BaseModel):
    labels: dict[str, Label] = {}
    flags: dict[str, str] = {}

    def apply(self, spans: list[Span], pieces: list[Piece], spec: Spec, allowed: set[str] | None = None) -> tuple[int, list[str]]:
        """Apply spans. Returns (pieces labelled, error messages). Valid spans are applied even if others fail."""
        order = {p.id: i for i, p in enumerate(pieces)}
        done, errors = 0, []
        for s in spans:
            where = f"span {s.start}–{s.end}"
            missing = [i for i in (s.start, s.end) if i not in order]
            if missing:
                hint = " (a split piece is now <id>a and <id>b)" if any(m + "a" in order for m in missing) else ""
                errors.append(f"{where}: unknown piece {', '.join(missing)}{hint}")
                continue
            a, b = order[s.start], order[s.end]
            if a > b:
                errors.append(f"{where}: start comes after end")
                continue
            ids = [p.id for p in pieces[a:b + 1]]
            outside = [i for i in ids if allowed is not None and i not in allowed]
            if outside:
                errors.append(f"{where}: {outside[0]} is outside the pieces you may label")
                continue
            role = spec.role(s.role)
            if role is None:
                errors.append(f"{where}: '{s.role}' is not a role in the Spec (roles: {', '.join(r.name for r in spec.roles)})")
                continue
            if role.kind == "unit" and (not s.n or any(k < 1 for k in s.n)):
                errors.append(f"{where}: role '{s.role}' is numbered content, so n is required (e.g. [5])")
                continue
            problem = _check_at(s, role.kind, spec)
            if problem:
                errors.append(f"{where}: {problem}")
                continue
            n = sorted(set(s.n)) if role.kind in ("unit", "locator") else []
            at = list(s.at) if (role.kind == "locator" or spec.position == "printed") else []
            for i in ids:
                self.labels[i] = Label(role=s.role, n=n, at=at)
                self.flags.pop(i, None)  # a new label is checked afresh
            done += len(ids)
        return done, errors

    def flag(self, piece_ids: list[str], reason: str) -> None:
        for i in piece_ids:
            self.flags[i] = reason

    def rename_split(self, old_id: str, new_ids: tuple[str, str]) -> None:
        """A split piece keeps its label (on both halves) until relabelled."""
        label, flag = self.labels.pop(old_id, None), self.flags.pop(old_id, None)
        for new in new_ids:
            if label:
                self.labels[new] = label.model_copy()
            if flag:
                self.flags[new] = flag
