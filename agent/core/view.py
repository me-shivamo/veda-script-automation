"""Text views of job state, written for agents to read (compact, IDs first)."""

from .assemble import Issue, Record
from .labels import LabelStore
from .pieces import Piece


def render_pieces(pieces: list[Piece], store: LabelStore | None = None) -> str:
    """One piece per line: ID | text {hints} [label]. A blank line marks a paragraph gap; pages are separated."""
    lines, page = [], None
    for p in pieces:
        if p.page != page:
            page = p.page
            lines.append(f"--- page {page} ---")
        elif p.blank_before:
            lines.append("")
        hints = list(p.hints)
        if not p.starts_line:
            hints.insert(0, "same-line")
        if p.printed_n:
            hints.insert(0, "n=" + ("-".join(map(str, (p.printed_n[0], p.printed_n[-1]))) if len(p.printed_n) > 1 else str(p.printed_n[0])))
        row = f"{p.id} | {p.text}"
        if hints:
            row += "  {" + ", ".join(hints) + "}"
        if store is not None:
            label = store.labels.get(p.id)
            row += f"  [{label.role}{' ' + ','.join(map(str, label.n)) if label and label.n else ''}]" if label else "  [unlabelled]"
            if p.id in store.flags:
                row += " [flagged]"
        lines.append(row)
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]} … {text[-half:]}"


def render_records(records: list[Record], limit: int = 160, local_name: str | None = None) -> str:
    """Rows as text. With local_name (e.g. 'mantra'), show only the unit number: used when the
    pieces are a slice of the book, where section numbers would be misleading."""
    if not records:
        return "(no records yet)"
    out = []
    for r in records:
        out.append(f"[{local_name} {r.n}]" if local_name else f"[{r.id}]")
        out += [f"  {name}: {_clip(text, limit) if text else '(empty)'}" for name, text in r.fields.items()]
    return "\n".join(out)


def render_issues(issues: list[Issue]) -> str:
    if not issues:
        return "No issues."
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    out = [f"{len(errors)} error(s), {len(warnings)} warning(s)."]
    out += [f"ERROR {i.kind}: {i.message}" for i in errors]
    out += [f"warning {i.kind}: {i.message}" for i in warnings]
    return "\n".join(out)
