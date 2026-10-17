"""The plan shown to the user before the full run: what the Analyst understood and what the output will look like.

Everything here is worked out by code from the Analyst's Spec, outline and
trial labels, so the preview is the real exporter's output for the trial pages,
and every example quoted is a real piece of the book.
"""

import re

from pydantic import BaseModel, Field

from .core.assemble import BuildResult, build, path_id
from .core.labels import LabelStore
from .core.pieces import Piece
from .core.spec import Spec, render_spec, spec_diff
from .export import block_rows, book_tree, table_rows


class OutlineItem(BaseModel):
    path: str = Field(description="Where this section sits, e.g. '1.3' for adhyaya 1, khanda 3.")
    label: str = Field(description="What the section is, in the book's words if possible, e.g. 'प्रथमाध्याये तृतीयः खण्डः'.")
    evidence: list[str] = Field(description="IDs of the pieces that show it: its title, closing line or a page locator, e.g. ['P0136'].")
    units: str = Field(default="", description="The unit numbers it holds as far as you saw, e.g. '3–12'.")


class Trial(BaseModel):
    """The labels from the Analyst's last trial run, kept so the preview can be redrawn after a structure edit."""

    pages: list[int] = []
    pieces: list[Piece] = []
    labels: LabelStore = Field(default_factory=LabelStore)


def trial_build(trial: Trial, spec: Spec) -> BuildResult | None:
    if not trial.pieces:
        return None
    store = LabelStore(labels={k: v for k, v in trial.labels.labels.items() if spec.role(v.role)}, flags=trial.labels.flags)
    return build(trial.pieces, store, spec, scope="chunk")


def ids_reliable(trial: Trial, spec: Spec) -> bool:
    """Section numbers in a trial are only right if the trial starts the book, or the pages say where they are."""
    if not spec.levels or spec.position == "printed" or (trial.pages and trial.pages[0] == 1):
        return True
    return any(spec.role(label.role) and spec.role(label.role).kind == "locator" for label in trial.labels.labels.values())


def preview(trial: Trial, spec: Spec, max_rows: int = 8) -> dict:
    """The trial pages in every output shape, exactly as the exporters write them."""
    built = trial_build(trial, spec)
    if built is None:
        return {"available": False}
    records, sections = built.records, built.sections
    reliable = ids_reliable(trial, spec)
    if not reliable:  # show only the unit numbers; the full run counts the sections from the start of the book
        records = [r.model_copy(update={"section": [0] * len(r.section), "id": str(r.n)}) for r in records]
        sections = []
    order = {p.id: i for i, p in enumerate(trial.pieces)}
    t_cols, t_rows = table_rows(records, spec, sections, order)
    b_cols, b_rows = block_rows(records, spec, sections, order)
    tree = book_tree(records[:max_rows], spec, [s for s in sections if any(r.section[:len(s.path)] == s.path for r in records[:max_rows])])
    return {
        "available": True,
        "pages": trial.pages,
        "ids_reliable": reliable,
        "table": {"columns": t_cols, "rows": t_rows[:max_rows]},
        "blocks": {"columns": b_cols, "rows": b_rows[:max_rows * 3]},
        "tree": {k: v for k, v in tree.items() if k != "structure"},  # the file starts with its schema; the preview shows the text
        "total_rows": len(t_rows),
    }


def _quote(ids: list[str], pieces: dict[str, Piece], limit: int = 90) -> list[dict]:
    out = []
    for i in ids:
        p = pieces.get(i)
        text = p.text if p else ""
        out.append({"id": i, "page": p.page if p else None, "text": text if len(text) <= limit else text[:limit] + " …", "found": p is not None})
    return out


def _id_example(spec: Spec) -> str:
    if spec.output.id_style == "columns":
        return " | ".join([*spec.level_names, spec.unit_name])
    return path_id([1] * len(spec.levels) + [5]) if spec.levels else "5"


def structure(spec: Spec) -> dict:
    return {
        "unit": spec.unit_name,
        "id_column": spec.id_column,
        "id_example": _id_example(spec),
        "levels": [{"name": lv.name, "optional": lv.optional, "fields": [f.model_dump() for f in lv.fields]} for lv in spec.levels],
        "fields": [f.model_dump() for f in spec.fields],
        "position": spec.position,
        "restart_numbering": spec.restart_numbering_each_section,
        "output": spec.output.model_dump(),
        "shared_text": spec.text.shared_text,
    }


def kinds(spec: Spec, pieces: dict[str, Piece]) -> list[dict]:
    rows = []
    for r in spec.roles:
        if r.kind in ("drop", "locator"):
            goes = "left out" + (" (used to check the numbering)" if r.kind == "locator" else "")
        else:
            where = {"unit": "", "attach_next": " (start, kept with the next " + spec.unit_name + ")",
                     "attach_prev": " (end, kept with the " + spec.unit_name + " before)"}[r.kind]
            goes = (r.field or "") + where
        rows.append({"role": r.name, "kind": r.kind, "goes_to": goes, "field": r.field, "description": readable(r.description, pieces),
                     "section": r.section.model_dump() if r.section else None, "examples": _quote(r.examples[:3], pieces)})
    return rows


_PIECE_ID = re.compile(r"\bP\d{4}[ab]?\b")


def readable(text: str, pieces: dict[str, Piece]) -> str:
    """Agents name lines by piece ID; people need the page and the words."""
    def name(match: re.Match) -> str:
        p = pieces.get(match.group(0))
        if p is None:
            return "a line"
        words = p.text if len(p.text) <= 40 else p.text[:40] + "…"
        return f"the line on page {p.page} (“{words}”)"
    return _PIECE_ID.sub(name, text)


def plan_view(spec: Spec, *, summary: str, limitations: list[str], outline: list[OutlineItem], questions: list[str],
              trial: Trial, pieces: dict[str, Piece], version: int = 1, previous: Spec | None = None, note: str = "") -> dict:
    """Everything the plan screen shows, as data."""
    return {
        "version": version,
        "summary": readable(summary, pieces),
        "limitations": [readable(x, pieces) for x in limitations],
        "questions": [readable(x, pieces) for x in questions],
        "outline": [{"path": o.path, "label": o.label, "units": o.units, "evidence": _quote(o.evidence, pieces)} for o in outline],
        "structure": structure(spec),
        "kinds": kinds(spec, pieces),
        "preview": preview(trial, spec),
        "changes": spec_diff(previous, spec) if previous is not None else [],
        "note": note,
        "rules": render_spec(spec),
        "columns": spec.columns,  # kept for older pages
    }


def describe_plan(view: dict) -> str:
    """The plan as text, for the command line and for analyst.md."""
    s = view["structure"]
    lines = [f"PLAN (version {view['version']})", "", view["summary"].strip(), ""]
    if view["changes"]:
        lines += ["WHAT CHANGED", ""] + [f"- {c}" for c in view["changes"]] + [""]
    lines += ["WHAT I FOUND IN THIS BOOK", ""]
    levels = " → ".join(lv["name"] + (" (optional)" if lv["optional"] else "") for lv in s["levels"]) or "no sections"
    lines.append(f"Structure: {levels} → {s['unit']}. Rows are placed by {'the location printed on each page' if s['position'] == 'printed' else 'counting section lines'}.")
    for o in view["outline"]:
        quoted = "; ".join(f"{e['id']}: {e['text']}" for e in o["evidence"])
        lines.append(f"  {o['path']}  {o['label']}" + (f"  ({s['unit']} {o['units']})" if o["units"] else "") + (f"  [{quoted}]" if quoted else ""))
    lines += ["", "KINDS OF TEXT AND WHERE EACH GOES", ""]
    for k in view["kinds"]:
        example = f"  e.g. {k['examples'][0]['text']}" if k["examples"] else ""
        lines.append(f"- {k['role']} → {k['goes_to']}{example}")
    out = s["output"]
    lines += ["", "YOUR OUTPUT FILE", "", f"Shape: {out['shape']}; ID: {s['id_example']}."]
    p = view["preview"]
    if p.get("available"):
        lines.append(f"Trial on pages {'–'.join(map(str, p['pages']))}" + ("" if p["ids_reliable"] else " (section numbers are worked out in the full run)") + ":")
        table = p["table"] if out["shape"] != "blocks" else p["blocks"]
        lines.append(" | ".join(table["columns"]))
        for row in table["rows"]:
            lines.append(" | ".join(cell if len(cell) <= 60 else cell[:60] + " …" for cell in row))
    if view["questions"]:
        lines += ["", "QUESTIONS FOR YOU", ""] + [f"{i + 1}. {q}" for i, q in enumerate(view["questions"])]
    if view["limitations"]:
        lines += ["", "LIMITATIONS", ""] + [f"- {item}" for item in view["limitations"]]
    lines += ["", "EXTRACTION RULES (the Spec)", "", view["rules"]]
    return "\n".join(lines)
