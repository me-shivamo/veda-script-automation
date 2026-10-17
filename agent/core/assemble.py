"""Turn labelled pieces into records.

Every character of output comes from piece text, unchanged: a field is its
pieces' text joined by single spaces, with only the OCR tool's '##' heading mark
left out.

Where a unit sits in the book is decided in one of two ways (Spec.position):
- counted: code counts sections from the lines that open and close them, in
  reading order, so a single wrong label cannot shift the numbering of the rest
  of the book;
- printed: the labels carry the section path printed on the page ('at'), for
  books whose OCR reading order cannot be trusted.
In both modes, lines that print where a page is (locators, such as running
headers) are checked against the place each unit was given.
"""

import re
from bisect import bisect_right
from typing import Literal

from pydantic import BaseModel

from .labels import LabelStore
from .pieces import Piece
from .spec import Spec

_MD_MARK = re.compile(r"^#{1,6}\s*")

Scope = Literal["chunk", "book"]


class Record(BaseModel):
    id: str
    section: list[int]
    n: int
    fields: dict[str, str]
    sources: dict[str, list[str]]


class SectionRecord(BaseModel):
    """Text that belongs to a whole section (its title, its closing line), when the Spec keeps it apart from the units."""

    id: str
    level: str
    path: list[int]
    fields: dict[str, str]
    sources: dict[str, list[str]]
    ends: list[str] = []  # fields printed at the end of the section (closing lines), as opposed to its start (titles)


class Issue(BaseModel):
    kind: str
    severity: Literal["error", "warning"]
    message: str
    pieces: list[str] = []
    unit: str | None = None


class BuildResult(BaseModel):
    records: list[Record]
    issues: list[Issue]
    dropped: list[str]
    sections: list[SectionRecord] = []

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def copy_text(piece: Piece) -> str:
    """The piece's text exactly as printed. '## ' is the OCR tool's heading mark, not book text."""
    return _MD_MARK.sub("", piece.text)


def join_texts(texts: list[str]) -> str:
    return " ".join(texts)


def path_id(numbers) -> str:
    """'1.3.5'. A 0 stands for an optional level the section does not have, and is left out."""
    return ".".join(str(x) for x in numbers if x != 0)


_FURNITURE_HINTS = {
    "page-top-number": "is the first line of its page and carries a page number, so it is probably a running header",
    "digits-only": "is only a number, so it is probably a printer's mark or page number",
    "footnote-like": "looks like a variant-reading footnote",
    "header-like": "is a short line in square brackets, so it is probably a running header the OCR garbled",
}
# Hints that make a dropped line acceptable even when it looks like a sentence.
_JUNK_HINTS = ("other-script",)


def _looks_like_content(piece: Piece) -> bool:
    if set(_FURNITURE_HINTS) & set(piece.hints) or any(h in _JUNK_HINTS for h in piece.hints):
        return False
    return len(piece.text) > 80 or "।" in piece.text or "॥" in piece.text


def _furniture_reason(piece: Piece) -> str | None:
    return next((reason for hint, reason in _FURNITURE_HINTS.items() if hint in piece.hints), None)


class _Unit:
    def __init__(self, section: tuple[int, ...], n: int):
        self.section, self.n = section, n
        self.parts: dict[str, list[tuple[Piece, str]]] = {}
        self.front: dict[str, int] = {}  # how many pieces were put before the unit's own text, per field

    @property
    def id(self) -> str:
        return path_id((*self.section, self.n))

    def add(self, field: str, piece: Piece, text: str, front: bool = False) -> None:
        parts = self.parts.setdefault(field, [])
        if front:
            parts.insert(self.front.get(field, 0), (piece, text))
            self.front[field] = self.front.get(field, 0) + 1
        else:
            parts.append((piece, text))


def _in_sequence(paths: list[tuple]) -> set[int]:
    """Indexes of the longest run of locator paths that never goes backwards. The others are outliers."""
    tails: list[tuple] = []
    tail_at: list[int] = []
    parent = [-1] * len(paths)
    for i, p in enumerate(paths):
        k = bisect_right(tails, p)
        if k == len(tails):
            tails.append(p)
            tail_at.append(i)
        else:
            tails[k], tail_at[k] = p, i
        parent[i] = tail_at[k - 1] if k else -1
    keep, i = set(), tail_at[-1] if tail_at else -1
    while i >= 0:
        keep.add(i)
        i = parent[i]
    return keep


def build(pieces: list[Piece], store: LabelStore, spec: Spec, scope: Scope = "book", start: tuple | None = None) -> BuildResult:
    """Walk the pieces in reading order and build records, section records and issues.

    start is used internally: the section path an extract starts in, when the first page's locator could mean two things.
    """
    issues: list[Issue] = []
    dropped: list[str] = []
    seen: set[tuple] = set()
    printed = spec.position == "printed"
    # In 'printed' books the OCR order is not trusted, so checks that rely on it only warn.
    order_checks = {"field_number_backwards", "order_broken", "text_after_section_end", "number_out_of_order"}

    def issue(kind: str, severity: str, message: str, piece: Piece | None = None, unit: str | None = None) -> None:
        key = (kind, unit, None if unit else (piece.id if piece else None))
        if key in seen:
            return
        seen.add(key)
        if printed and kind in order_checks:
            severity = "warning"
        # A piece flagged for human review does not block the agent, except that real text may
        # never be dropped: a flag cannot make that acceptable.
        if piece and piece.id in store.flags:
            if severity == "error" and kind != "dropped_content":
                severity = "warning"
            message = f"{message} (flagged: {store.flags[piece.id]})"
        issues.append(Issue(kind=kind, severity=severity, message=message, pieces=[piece.id] if piece else [], unit=unit))

    levels = len(spec.levels)
    labelled = [(i, p, store.labels.get(p.id)) for i, p in enumerate(pieces)]
    role_of = {p.id: spec.role(label.role) for _, p, label in labelled if label}

    # --- locators: where the pages say they are ------------------------------------------
    locators = [(p, tuple(label.at)) for _, p, label in labelled
                if label and role_of.get(p.id) and role_of[p.id].kind == "locator" and label.at]
    keep = _in_sequence([at for _, at in locators])
    page_locs: dict[int, list[tuple]] = {}
    for k, (p, at) in enumerate(locators):
        if k in keep:
            page_locs.setdefault(p.page, []).append(at)
        else:
            issue("locator_out_of_sequence", "warning",
                  f"{p.id} is read as {path_id(at)} but the locators around it say otherwise, so it is ignored. "
                  f"Fine if the OCR garbled this header: {p.text[:60]}", p)
    page_edges: dict[int, set[int]] = {}
    for _, p, label in labelled:
        role = role_of.get(p.id)
        if role and role.section:
            page_edges.setdefault(p.page, set()).add(spec.level_names.index(role.section.level))

    def position_ok(section: tuple, page: int) -> bool:
        pages = [page] if page in page_locs else [page - 1, page + 1]
        locs = [at for pg in pages for at in page_locs.get(pg, [])]
        if not locs:
            return True
        edges = set().union(*(page_edges.get(pg, set()) for pg in pages))
        for loc in locs:
            m = min(len(loc), len(section))
            if section[:m] == loc[:m]:
                return True
            # A page where a section ends or begins can hold the neighbouring section too.
            for k in edges:
                if k < m and section[:k] == loc[:k] and abs(section[k] - loc[k]) == 1:
                    return True
        return False

    # --- counted sections ----------------------------------------------------------------
    counters, is_open = [0] * levels, [False] * levels
    first_unit_index = next((i for i, p, label in labelled if label and role_of.get(p.id) and role_of[p.id].kind == "unit"), None)
    if not printed and locators and keep and first_unit_index is not None:
        # An extract that starts mid-book: the first locator says where it starts.
        first_loc = locators[min(keep)]
        if start is None and first_loc[0].page <= pieces[first_unit_index].page:
            # A header may name the section a page ends in rather than the one it begins with. If a section closes on
            # that page, try both readings and keep the one the later locators agree with.
            ends = sorted({spec.level_names.index(role_of[p.id].section.level) for _, p, label in labelled
                           if label and role_of.get(p.id) and role_of[p.id].section and role_of[p.id].section.edge == "end"
                           and p.page == first_loc[0].page})
            at = first_loc[1]
            candidates = [at] + [(*at[:k], at[k] - 1, *at[k + 1:]) for k in ends if k < len(at) and at[k] > 1]
            if len(candidates) > 1:
                conflicts = [sum(i.kind == "position_conflict" for i in build(pieces, store, spec, scope, start=c).issues) for c in candidates]
                start = candidates[conflicts.index(min(conflicts))]
        if start is not None:
            first_loc = (first_loc[0], start)
        if first_loc[0].page <= pieces[first_unit_index].page:
            start_edges = [spec.level_names.index(role_of[p.id].section.level) for i, p, label in labelled[:first_unit_index]
                           if label and role_of.get(p.id) and role_of[p.id].section and role_of[p.id].section.edge == "start"]
            opened_by_title = min(start_edges) if start_edges else levels
            for j, value in enumerate(first_loc[1][:levels]):
                if j < opened_by_title:
                    counters[j], is_open[j] = value, True
                elif j == opened_by_title:
                    counters[j] = max(value - 1, 0)

    def open_level(i: int) -> None:
        counters[i] += 1
        is_open[i] = True
        for j in range(i + 1, levels):
            counters[j], is_open[j] = 0, False

    def close_level(i: int) -> None:
        for j in range(i, levels):
            is_open[j] = False

    def level_ahead(i: int, start: int) -> bool:
        """True if a line opening or closing level i comes before the section above it ends."""
        for _, p, _ in labelled[start:]:
            role = role_of.get(p.id)
            if role and role.section:
                k = spec.level_names.index(role.section.level)
                if k == i:
                    return True
                if k < i:
                    return False
        return False

    def open_for_unit(index: int) -> None:
        for i in range(levels):
            if is_open[i]:
                continue
            if spec.levels[i].optional and counters[i] == 0 and not level_ahead(i, index):
                is_open[i] = True  # this section has no subsections of level i: counter stays 0
                for j in range(i + 1, levels):
                    counters[j], is_open[j] = 0, False
            else:
                open_level(i)

    # --- the walk ----------------------------------------------------------------------
    units: dict[tuple, _Unit] = {}
    sections: dict[tuple, dict[str, list[tuple[Piece, str]]]] = {}
    section_ends: dict[tuple, set[str]] = {}
    pending_next: list[tuple[str, Piece, str, tuple]] = []  # (field, piece, text, at) waiting for the next unit
    deferred: list[tuple[str, str, tuple, Piece, str]] = []  # (kind, field, at, piece, text) for attach spans placed by 'at'
    last_units: list[_Unit] = []
    closed_by: Piece | None = None  # the section-closing line seen since the last unit, if any
    after_close: list[tuple[Piece, Piece, int]] = []  # (piece, closing line, units so far) for text attached back past a close
    first_section: tuple | None = None
    max_n: dict[tuple, int] = {}
    first_n: dict[tuple, int] = {}
    last_in_field: dict[tuple, int] = {}
    closed: dict[tuple, str] = {}  # (section, role, numbers) -> the piece whose marker closed that passage
    running_max = 0  # for books numbered straight through sections
    marker_seen: dict[tuple, str] = {}  # (unit key, field) -> piece whose own number was printed, for 'printed' books
    checked_pages: set[tuple] = set()

    def place(unit_section: tuple, unit: _Unit | None, field: str, piece: Piece, text: str, front: bool = False, end: bool = False) -> None:
        target = spec.section_field(field)
        if target:
            k, f = target
            path = unit_section[:k + 1]
            sections.setdefault(path, {}).setdefault(f.name, []).append((piece, text))
            if end:
                section_ends.setdefault(path, set()).add(f.name)
        elif unit is not None:
            unit.add(field, piece, text, front)

    for index, piece, label in labelled:
        if label is None:
            issue("unlabelled", "error", f"{piece.id} has no label", piece)
            continue
        role = role_of.get(piece.id)
        if role is None:
            issue("unknown_role", "error", f"{piece.id} has role '{label.role}', which is not in the Spec", piece)
            continue
        edge = role.section
        if edge and edge.edge == "start" and not printed:
            open_level(spec.level_names.index(edge.level))

        if role.kind == "locator":
            dropped.append(piece.id)
            continue
        if role.kind == "drop":
            dropped.append(piece.id)
            if _looks_like_content(piece):
                issue("dropped_content", "error",
                      f"{piece.id} is dropped as '{role.name}' but looks like book text: {piece.text[:60]}. "
                      f"Only page furniture may be dropped. Keep it with the unit it belongs to (an attach role for "
                      f"invocations, titles, closing verses and colophons). A line that prints where the page is "
                      f"belongs to a locator role.", piece)
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
            if printed:
                section = tuple(label.at)
            else:
                open_for_unit(index)
                section = tuple(counters)
            if first_section is None:
                first_section = section
            lenient = scope == "chunk" and section == first_section
            targets: list[_Unit] = []
            for k in label.n:
                key = (section, k)
                if key not in units:
                    where = f"{spec.levels[-1].name} {path_id(section)}" if levels else "the book"
                    if printed:
                        pass  # numbering is checked on the whole set at the end
                    elif section not in max_n:
                        expected = 1 if spec.restart_numbering_each_section or not levels else running_max + 1
                        if k != expected and not lenient and not (scope == "chunk" and not spec.restart_numbering_each_section):
                            # The text may be an extract that starts mid-book, so the very first section only warns.
                            issue("section_start_number", "warning" if section == first_section else "error",
                                  f"{where} starts at {spec.unit_name} {k}, expected {expected}. A section-end line may be unlabelled, or n is wrong at {piece.id}.",
                                  piece, path_id((*section, k)))
                    elif k > max_n[section] + 1:
                        issue("number_gap", "error",
                              f"In {where}, {spec.unit_name} {max_n[section]} is followed by {k} at {piece.id}; {max_n[section] + 1} is missing.",
                              piece, path_id((*section, k)))
                    elif k < max_n[section] and not (lenient and k < first_n[section]):
                        issue("number_out_of_order", "error",
                              f"In {where}, {spec.unit_name} {k} first appears at {piece.id}, after {spec.unit_name} {max_n[section]}.",
                              piece, path_id((*section, k)))
                    units[key] = _Unit(section, k)
                max_n[section] = max(max_n.get(section, k), k)
                first_n.setdefault(section, k)
                running_max = max(running_max, k)
                targets.append(units[key])
            if (section, piece.page) not in checked_pages:
                checked_pages.add((section, piece.page))
                if not position_ok(section, piece.page):
                    near = sorted({path_id(at) for pg in (piece.page - 1, piece.page, piece.page + 1) for at in page_locs.get(pg, [])})
                    how = ("its 'at'" if printed else "counting the section lines before it")
                    issue("position_conflict", "error",
                          f"{piece.id} is placed in {spec.levels[-1].name if levels else 'section'} {path_id(section)} by {how}, "
                          f"but the locator lines on and around page {piece.page} say {', '.join(near)}. "
                          + ("Check 'at' against the page's locator." if printed else
                             "A section line is probably missing or mislabelled before it, or a misread closing line closes the wrong level."),
                          piece, path_id(section) + f"@{piece.page}")
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
            if printed:
                for unit in targets:
                    mark = ((unit.section, unit.n), role.field)
                    if mark in marker_seen:
                        issue("marker_not_last", "warning",
                              f"{piece.id} comes after {marker_seen[mark]}, which already ends {spec.unit_name} {unit.id}'s '{role.field}' with its number. "
                              f"The OCR may have printed this text out of order: check the scan. Code keeps the pieces in reading order.",
                              piece, unit.id + "/" + role.field)
                    elif piece.printed_n == label.n:
                        marker_seen[mark] = piece.id
            if piece.printed_n and piece.printed_n != label.n:
                issue("printed_number_differs", "warning",
                      f"{piece.id} prints number {piece.printed_n} but is placed as {spec.unit_name} {label.n}. The text keeps the "
                      f"printed number. Fine if the OCR misread the digit (check the scan); otherwise fix n.", piece)

            for field, p, text, at in pending_next:
                if at and targets[0].section[:len(at)] != at:
                    deferred.append(("attach_next", field, at, p, text))  # its own section says it belongs elsewhere
                else:
                    place(targets[0].section, targets[0], field, p, text)
            pending_next.clear()
            text = copy_text(piece)
            for unit in (targets if spec.text.shared_text == "copy" else targets[:1]):
                unit.add(role.field, piece, text)
            last_units, closed_by = targets, None
        elif printed and label.at and role.section:
            # A line that opens or closes a section belongs at that section's start or end, wherever the OCR printed it.
            deferred.append((role.kind, role.field, tuple(label.at), piece, copy_text(piece)))
        elif role.kind == "attach_next":
            pending_next.append((role.field, piece, copy_text(piece), tuple(label.at) if printed else ()))
        elif printed and label.at and last_units and last_units[-1].section[:len(label.at)] != tuple(label.at):
            deferred.append((role.kind, role.field, tuple(label.at), piece, copy_text(piece)))  # the unit before is in another section
        else:  # attach_prev
            text = copy_text(piece)
            if last_units and pending_next:
                waiting = pending_next[-1][1]
                issue("order_broken", "error",
                      f"{piece.id} ('{role.name}') attaches to the unit before, but {waiting.id} above it is waiting for the next unit, "
                      f"so the output would put {piece.id} ahead of text printed before it. {piece.id} most likely begins the next "
                      f"passage, or {waiting.id} is not a heading.", piece)
            if last_units and closed_by and role.section is None:
                after_close.append((piece, closed_by, len(units)))
            if last_units:
                place(last_units[-1].section, last_units[-1], role.field, piece, text, end=True)
            else:
                if scope == "book":
                    issue("nothing_before", "warning", f"{piece.id} ('{role.name}') should attach to the unit before it, but none exists yet; kept with the next unit.", piece)
                pending_next.append((role.field, piece, text, ()))

        if edge and edge.edge == "end":
            if not printed:
                close_level(spec.level_names.index(edge.level))
            if last_units:
                closed_by = piece

    # Text after a section's closing line belongs to what follows, unless the book ends there (a final colophon).
    for piece, closer, units_then in after_close:
        if len(units) > units_then:
            issue("text_after_section_end", "error",
                  f"{piece.id} is attached to the unit before, but it comes after the closing line {closer.id} ({closer.text[:40]}) "
                  f"and the book continues after it. Text after a closing line belongs to the next section: usually a heading "
                  f"or the start of the next unit.", piece)

    # Attach spans that name their section ('printed' books): first unit for text before, last unit for text after.
    for kind, field, at, piece, text in deferred:
        in_section = sorted((u for (sec, _), u in units.items() if sec[:len(at)] == at), key=lambda u: (u.section, u.n))
        if spec.section_field(field):
            place(at + (0,) * (levels - len(at)), None, field, piece, text, end=kind == "attach_prev")
        elif in_section:
            unit = in_section[-1] if kind == "attach_prev" else in_section[0]
            place(unit.section, unit, field, piece, text, front=kind == "attach_next", end=kind == "attach_prev")
        else:
            issue("no_such_section", "error",
                  f"{piece.id} is attached to section {path_id(at)}, but no {spec.unit_name} is placed there.", piece)
            pending_next.append((field, piece, text, ()))

    if pending_next and scope == "book":
        ids = [p.id for _, p, _, _ in pending_next]
        if last_units:
            for field, p, text, _ in pending_next:
                place(last_units[-1].section, last_units[-1], field, p, text)
            issues.append(Issue(kind="trailing_text", severity="warning", pieces=ids, unit=last_units[-1].id,
                                message=f"{', '.join(ids)} had no following unit, so the text was kept at the end of {last_units[-1].id}."))
        else:
            issues.append(Issue(kind="no_units", severity="error", pieces=ids, message="No numbered units were labelled, so nothing can be assembled."))

    if printed:
        _check_printed_numbering(units, spec, scope, issue)

    records = []
    edge_pages = {pieces[0].page, pieces[-1].page} if pieces else set()
    unit_fields = {r.field for r in spec.roles if r.kind == "unit"}
    position_of = {p.id: i for i, p in enumerate(pieces)}
    first_field = spec.fields[0].name
    for unit in sorted(units.values(), key=lambda u: (u.section, u.n)):
        if not printed:
            # In a book read in order, a unit's first column (its root text) is printed before its other numbered text.
            numbered = lambda field: [p for p, _ in unit.parts.get(field, []) if role_of[p.id] and role_of[p.id].kind == "unit"]  # noqa: E731
            root = numbered(first_field)
            others = [p for f in unit.parts if f != first_field for p in numbered(f)]
            if root and others:
                early = min(others, key=lambda p: position_of[p.id])
                if position_of[early.id] < position_of[root[0].id] and len(store.labels[early.id].n) == 1:
                    issue("before_root", "error",
                          f"{early.id} is labelled '{store.labels[early.id].role}' for {spec.unit_name} {unit.id}, but it comes before that "
                          f"{spec.unit_name}'s '{first_field}' text at {root[0].id}. In this book the root text comes first, so {early.id} "
                          f"is probably the opening of the root text (a verse line or speaker line), or it belongs to the {spec.unit_name} before.",
                          early, unit.id + "/before_root")
        if scope == "book":
            for field, parts in unit.parts.items():
                last = parts[-1][0]
                if field in unit_fields and last.text.rstrip().endswith("-") and not last.text.rstrip().endswith("--"):
                    issue("passage_cut", "error",
                          f"{spec.unit_name} {unit.id}'s '{field}' ends in a broken word at {last.id} ({last.text[-30:]}), so its last part is "
                          f"printed somewhere else: after a page header, after a closing line, or out of order on the page. Find the piece "
                          f"that continues it and give it the same role, n" + (" and at" if printed else "") + ". If the book really breaks off here, flag it.",
                          last, unit.id + "/" + field)
        fields = {f.name: join_texts([text for _, text in unit.parts.get(f.name, [])]) for f in spec.fields}
        sources = {name: [p.id for p, _ in parts] for name, parts in unit.parts.items()}
        records.append(Record(id=unit.id, section=list(unit.section), n=unit.n, fields=fields, sources=sources))
        on_edge = any(p.page in edge_pages for parts in unit.parts.values() for p, _ in parts)
        for f in spec.fields:
            if f.required and not fields[f.name]:
                if scope == "book":
                    issue("missing_field", "error", f"{spec.unit_name} {unit.id} has no '{f.name}'.", unit=unit.id + "/" + f.name)
                elif not on_edge:
                    issue("missing_field", "warning", f"{spec.unit_name} {unit.n} has no '{f.name}' in this chunk. Check its pieces are labelled.", unit=unit.id + "/" + f.name)
    section_records = []
    for path, parts in sorted(sections.items()):
        k = len(path) - 1
        section_records.append(SectionRecord(
            id=path_id(path), level=spec.levels[k].name, path=list(path),
            fields={f.name: join_texts([text for _, text in parts.get(f.name, [])]) for f in spec.levels[k].fields},
            sources={name: [p.id for p, _ in items] for name, items in parts.items()},
            ends=sorted(section_ends.get(path, set())),
        ))
    return BuildResult(records=records, issues=issues, dropped=dropped, sections=section_records)


def _check_printed_numbering(units: dict, spec: Spec, scope: Scope, issue) -> None:
    """Numbering checks for 'printed' books, on the whole set of units rather than in reading order."""
    by_section: dict[tuple, list[int]] = {}
    for section, n in sorted(units):
        by_section.setdefault(section, []).append(n)
    name = spec.levels[-1].name
    ordered = sorted(by_section)
    previous_last = None
    for i, section in enumerate(ordered):
        numbers = by_section[section]
        where = f"{name} {path_id(section)}"
        expected = 1 if spec.restart_numbering_each_section else (previous_last + 1 if previous_last else numbers[0])
        if numbers[0] != expected and scope == "book":
            issue("section_start_number", "warning" if i == 0 else "error",
                  f"{where} starts at {spec.unit_name} {numbers[0]}, expected {expected}. Check the 'at' and n of its first passage.",
                  unit=path_id((*section, numbers[0])))
        for a, b in zip(numbers, numbers[1:]):
            if b > a + 1:
                issue("number_gap", "error" if scope == "book" else "warning",
                      f"In {where}, {spec.unit_name} {a} is followed by {b}; {', '.join(str(x) for x in range(a + 1, b))} missing. "
                      f"Its text may be labelled with another 'at' or n.", unit=path_id((*section, a + 1)))
        previous_last = numbers[-1]
        if i and scope == "book":
            before = ordered[i - 1]
            for k in range(min(len(before), len(section))):
                if before[:k] == section[:k] and section[k] > before[k] + 1 and before[k] != 0:
                    issue("section_gap", "warning",
                          f"No {spec.unit_name} is placed in {spec.levels[k].name} {path_id((*section[:k], before[k] + 1))}: "
                          f"{path_id(before)} is followed by {path_id(section)}.", unit=path_id(section))
                    break
                if before[k] != section[k]:
                    break

