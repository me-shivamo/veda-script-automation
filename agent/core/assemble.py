"""Turn labelled pieces into records.

Every character of output comes from piece text. Code computes the row IDs by
counting section edges and reading the unit numbers on the labels, so a single
wrong label cannot shift the numbering of the rest of the book.
"""

import re
from typing import Literal

from pydantic import BaseModel

from .devanagari import VERSE_MARKER, like_digits
from .labels import Label, LabelStore
from .pieces import Piece
from .spec import RoleDef, Spec

_MD_MARK = re.compile(r"^#{1,6}\s*")
_LEADING_NOISE = re.compile(r"^(?:[•·●▪∙]\s*|\.\s+)")
_QUOTES = str.maketrans({c: "'" for c in '"“”„«»'})

Scope = Literal["chunk", "book"]


class Record(BaseModel):
    id: str
    section: list[int]
    n: int
    fields: dict[str, str]
    sources: dict[str, list[str]]


class Issue(BaseModel):
    kind: str
    severity: Literal["error", "warning"]
    message: str
    pieces: list[str] = []
    unit: str | None = None


class Change(BaseModel):
    piece: str
    kind: str
    before: str
    after: str


class BuildResult(BaseModel):
    records: list[Record]
    issues: list[Issue]
    changes: list[Change]
    dropped: list[str]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def _rewrite_marker(text: str, n: list[int]) -> str | None:
    last = None
    for last in VERSE_MARKER.finditer(text):
        pass
    if last is None:
        return None
    if last.group(2) is None:
        if len(n) != 1:
            return None
        return text[:last.start(1)] + like_digits(n[0], last.group(1)) + text[last.end(1):]
    if len(n) < 2:
        return None
    text = text[:last.start(2)] + like_digits(n[-1], last.group(2)) + text[last.end(2):]
    return text[:last.start(1)] + like_digits(n[0], last.group(1)) + text[last.end(1):]


def _clean(piece: Piece, label: Label, role: RoleDef, spec: Spec, changes: list[Change]) -> str:
    text = _MD_MARK.sub("", piece.text)  # '## ' is the OCR tool's markup, not book text

    def record(kind: str, new: str) -> str:
        if new != text:
            changes.append(Change(piece=piece.id, kind=kind, before=text, after=new))
        return new

    if spec.text.strip_leading_noise:
        text = record("leading_noise_removed", _LEADING_NOISE.sub("", text))
    if spec.text.fix_verse_numbers and role.kind == "unit" and piece.printed_n and piece.printed_n != label.n:
        text = record("verse_number_fixed", _rewrite_marker(text, label.n) or text)
    if spec.text.quotes == "single":
        text = record("quotes_changed", text.translate(_QUOTES))
    return text


def _join(parts: list[tuple[Piece, str]], spec: Spec, changes: list[Change]) -> str:
    out, prev = "", None
    for piece, text in parts:
        if prev is None:
            out = text
        elif spec.text.line_end_hyphen == "join" and prev.ends_line and out.endswith("-") and not out.endswith("--"):
            changes.append(Change(piece=prev.id, kind="hyphen_joined", before=out[-12:] + " " + text[:12], after=out[-12:-1] + text[:12]))
            out = out[:-1] + text
        else:
            out += " " + text
        prev = piece
    return re.sub(r"\s+", " ", out).strip()


_FURNITURE_HINTS = {
    "page-top-number": "is the first line of its page and carries a page number, so it is probably a running header",
    "digits-only": "is only a number, so it is probably a printer's mark or page number",
    "footnote-like": "looks like a variant-reading footnote",
}


def _looks_like_content(piece: Piece) -> bool:
    if set(_FURNITURE_HINTS) & set(piece.hints):
        return False
    return len(piece.text) > 80 or "॥" in piece.text


def _furniture_reason(piece: Piece) -> str | None:
    return next((reason for hint, reason in _FURNITURE_HINTS.items() if hint in piece.hints), None)


class _Unit:
    def __init__(self, section: tuple[int, ...], n: int):
        self.section, self.n = section, n
        self.parts: dict[str, list[tuple[Piece, str]]] = {}

    @property
    def id(self) -> str:
        return ".".join(str(x) for x in (*self.section, self.n))

    def add(self, field: str, piece: Piece, text: str) -> None:
        self.parts.setdefault(field, []).append((piece, text))


def build(pieces: list[Piece], store: LabelStore, spec: Spec, scope: Scope = "book") -> BuildResult:
    """Walk the pieces in reading order and build records, issues and the change log."""
    issues: list[Issue] = []
    changes: list[Change] = []
    dropped: list[str] = []
    seen: set[tuple] = set()

    def issue(kind: str, severity: str, message: str, piece: Piece | None = None, unit: str | None = None) -> None:
        key = (kind, unit, None if unit else (piece.id if piece else None))
        if key in seen:
            return
        seen.add(key)
        # A piece flagged for human review does not block the agent.
        if piece and piece.id in store.flags and severity == "error":
            severity, message = "warning", f"{message} (flagged: {store.flags[piece.id]})"
        issues.append(Issue(kind=kind, severity=severity, message=message, pieces=[piece.id] if piece else [], unit=unit))

    levels = len(spec.levels)
    counters, is_open = [0] * levels, [False] * levels
    units: dict[tuple, _Unit] = {}
    pending_next: list[tuple[str, Piece, str]] = []
    last_units: list[_Unit] = []
    first_section: tuple | None = None
    max_n: dict[tuple, int] = {}
    first_n: dict[tuple, int] = {}
    last_in_field: dict[tuple, int] = {}
    closed: dict[tuple, str] = {}  # (section, role, numbers) -> the piece whose marker closed that passage
    running_max = 0  # for books numbered straight through sections

    def open_level(i: int) -> None:
        counters[i] += 1
        is_open[i] = True
        for j in range(i + 1, levels):
            counters[j], is_open[j] = 0, False

    def close_level(i: int) -> None:
        for j in range(i, levels):
            is_open[j] = False

    for piece in pieces:
        label = store.labels.get(piece.id)
        if label is None:
            issue("unlabelled", "error", f"{piece.id} has no label", piece)
            continue
        role = spec.role(label.role)
        if role is None:
            issue("unknown_role", "error", f"{piece.id} has role '{label.role}', which is not in the Spec", piece)
            continue
        edge = role.section
        if edge and edge.edge == "start":
            open_level(spec.levels.index(edge.level))

        if role.kind == "drop":
            dropped.append(piece.id)
            if _looks_like_content(piece):
                issue("dropped_content", "error",
                      f"{piece.id} is dropped as '{role.name}' but looks like book text: {piece.text[:60]}. "
                      f"Keep it with the unit it belongs to, or flag it if it really is not part of the text.", piece)
        elif _furniture_reason(piece):
            issue("kept_furniture", "error",
                  f"{piece.id} is kept as '{role.name}' but {_furniture_reason(piece)}: {piece.text[:60]}. "
                  f"If it is page furniture, give it a drop role; the passage around it continues with the same role and n.", piece)

        if role.kind in ("attach_next", "attach_prev") and not piece.starts_line:
            issue("attached_mid_line", "error",
                  f"{piece.id} is labelled '{role.name}' but it continues the printed line of the piece before it: {piece.text[:60]}. "
                  f"Text that follows a verse number on the same line normally begins the next numbered passage.", piece)

        if role.kind == "attach_next" and role.section is None and ("।" in piece.text or "॥" in piece.text):
            issue("attached_sentence", "error",
                  f"{piece.id} is labelled '{role.name}' but it contains a danda, so it reads as a sentence or verse line, not a heading: {piece.text[:60]}. "
                  f"If it is root text or commentary, give it that role and the number of the passage it belongs to (usually the one that follows).", piece)

        if role.kind == "drop":
            pass
        elif role.kind == "unit":
            for i in range(levels):
                if not is_open[i]:
                    open_level(i)
            section = tuple(counters)
            if first_section is None:
                first_section = section
            lenient = scope == "chunk" and section == first_section
            targets: list[_Unit] = []
            for k in label.n:
                key = (section, k)
                if key not in units:
                    where = f"{spec.levels[-1]} {'.'.join(map(str, section))}" if levels else "the book"
                    if section not in max_n:
                        expected = 1 if spec.restart_numbering_each_section or not levels else running_max + 1
                        if k != expected and not lenient and not (scope == "chunk" and not spec.restart_numbering_each_section):
                            # The text may be an extract that starts mid-book, so the very first section only warns.
                            issue("section_start_number", "warning" if section == first_section else "error",
                                  f"{where} starts at {spec.unit_name} {k}, expected {expected}. A section-end line may be unlabelled, or n is wrong at {piece.id}.",
                                  piece, ".".join(map(str, (*section, k))))
                    elif k > max_n[section] + 1:
                        issue("number_gap", "error",
                              f"In {where}, {spec.unit_name} {max_n[section]} is followed by {k} at {piece.id}; {max_n[section] + 1} is missing.",
                              piece, ".".join(map(str, (*section, k))))
                    elif k < max_n[section] and not (lenient and k < first_n[section]):
                        issue("number_out_of_order", "error",
                              f"In {where}, {spec.unit_name} {k} first appears at {piece.id}, after {spec.unit_name} {max_n[section]}.",
                              piece, ".".join(map(str, (*section, k))))
                    units[key] = _Unit(section, k)
                max_n[section] = max(max_n.get(section, k), k)
                first_n.setdefault(section, k)
                running_max = max(running_max, k)
                targets.append(units[key])
            field_key = (section, role.field)
            if field_key in last_in_field and label.n[-1] < last_in_field[field_key]:
                issue("field_number_backwards", "error",
                      f"In column '{role.field}', {piece.id} is numbered {label.n[-1]} but comes after {last_in_field[field_key]}. "
                      f"Either n is wrong or a section-end line between them is unlabelled.", piece)
            last_in_field[field_key] = max(last_in_field.get(field_key, 0), label.n[-1])
            passage = (section, role.name, tuple(label.n))
            if passage in closed:
                issue("text_after_number", "error",
                      f"{piece.id} is labelled '{role.name}' {label.n}, but a '{role.name}' {label.n} passage already ended at {closed[passage]}, which ends with a printed number. "
                      f"A passage takes the number printed at its END. So either {piece.id} belongs to the next passage "
                      f"(the one whose number is printed next after it), or {closed[passage]} has the wrong role "
                      f"(for example, it is the last line of the root text, not commentary).", piece)
            elif piece.printed_n:
                closed[passage] = piece.id
            if piece.printed_n and piece.printed_n != label.n:
                issue("printed_number_differs", "warning",
                      f"{piece.id} prints number {piece.printed_n} but is labelled {label.n}. Fine if the OCR misread the digit; otherwise fix n.", piece)

            for field, p, text in pending_next:
                targets[0].add(field, p, text)
            pending_next.clear()
            text = _clean(piece, label, role, spec, changes)
            for unit in (targets if spec.text.shared_text == "copy" else targets[:1]):
                unit.add(role.field, piece, text)
            last_units = targets
        elif role.kind == "attach_next":
            pending_next.append((role.field, piece, _clean(piece, label, role, spec, changes)))
        else:  # attach_prev
            text = _clean(piece, label, role, spec, changes)
            if last_units:
                last_units[-1].add(role.field, piece, text)
            else:
                if scope == "book":
                    issue("nothing_before", "warning", f"{piece.id} ('{role.name}') should attach to the unit before it, but none exists yet; kept with the next unit.", piece)
                pending_next.append((role.field, piece, text))

        if edge and edge.edge == "end":
            close_level(spec.levels.index(edge.level))

    if pending_next and scope == "book":
        ids = [p.id for _, p, _ in pending_next]
        if last_units:
            for field, p, text in pending_next:
                last_units[-1].add(field, p, text)
            issues.append(Issue(kind="trailing_text", severity="warning", pieces=ids, unit=last_units[-1].id,
                                message=f"{', '.join(ids)} had no following unit, so the text was kept at the end of {last_units[-1].id}."))
        else:
            issues.append(Issue(kind="no_units", severity="error", pieces=ids, message="No numbered units were labelled, so nothing can be assembled."))

    records = []
    edge_pages = {pieces[0].page, pieces[-1].page} if pieces else set()
    for unit in units.values():
        fields = {f.name: _join(unit.parts.get(f.name, []), spec, changes) for f in spec.fields}
        sources = {name: [p.id for p, _ in parts] for name, parts in unit.parts.items()}
        records.append(Record(id=unit.id, section=list(unit.section), n=unit.n, fields=fields, sources=sources))
        on_edge = any(p.page in edge_pages for parts in unit.parts.values() for p, _ in parts)
        for f in spec.fields:
            if f.required and not fields[f.name]:
                if scope == "book":
                    issue("missing_field", "error", f"{spec.unit_name} {unit.id} has no '{f.name}'.", unit=unit.id + "/" + f.name)
                elif not on_edge:
                    issue("missing_field", "warning", f"{spec.unit_name} {unit.n} has no '{f.name}' in this chunk. Check its pieces are labelled.", unit=unit.id + "/" + f.name)
    return BuildResult(records=records, issues=issues, changes=changes, dropped=dropped)
