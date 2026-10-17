"""Prove that the output is the book's text, character for character.

This check runs on every finished book, independently of how the rows were
built. It confirms three things:

1. The pieces are the input lines, in order, with nothing added or altered
   (pieces only cut a line at whitespace).
2. Every output field is exactly its pieces' text joined by single spaces.
3. Every piece is either in the output or in the list of removed lines.
"""

from .assemble import Issue, Record, copy_text, join_texts
from .pieces import Piece, split_pages


def _lines(text: str, pages: tuple[int, int] | None) -> list[str]:
    return [line.strip() for number, page in enumerate(split_pages(text), start=1)
            if pages is None or pages[0] <= number <= pages[1] for line in page if line.strip()]


def _line_groups(pieces: list[Piece]) -> list[list[Piece]]:
    groups: list[list[Piece]] = []
    for piece in pieces:
        if piece.starts_line or not groups:
            groups.append([])
        groups[-1].append(piece)
    return groups


def check_fidelity(source: str, pieces: list[Piece], records: list[Record], dropped: list[str],
                   pages: tuple[int, int] | None = None) -> list[Issue]:
    """Issues of kind 'not_verbatim' (always errors); empty when the output is the book text exactly.

    pages limits the check to the page range the run processed.
    """
    issues: list[Issue] = []

    def fail(message: str, pieces_: list[str] | None = None, unit: str | None = None) -> None:
        issues.append(Issue(kind="not_verbatim", severity="error", message=message, pieces=pieces_ or [], unit=unit))

    lines, groups = _lines(source, pages), _line_groups(pieces)
    if len(lines) != len(groups):
        fail(f"The input has {len(lines)} lines of text but the pieces make {len(groups)}.")
    for line, group in zip(lines, groups):
        at = 0
        for piece in group:
            found = line.find(piece.text, at)
            if found < 0 or line[at:found].strip():
                fail(f"{piece.id} is not a copy of its input line: {piece.text[:60]}", [piece.id])
                break
            at = found + len(piece.text)
        else:
            if line[at:].strip():
                fail(f"The end of an input line is missing from the pieces: {line[at:][:60]}", [group[-1].id])

    by_id = {p.id: p for p in pieces}
    used: set[str] = set(dropped)
    for record in records:
        for field, text in record.fields.items():
            ids = record.sources.get(field, [])
            used.update(ids)
            if any(i not in by_id for i in ids):
                fail(f"Row {record.id} '{field}' refers to a piece that does not exist.", unit=record.id)
            elif text != join_texts([copy_text(by_id[i]) for i in ids]):
                fail(f"Row {record.id} '{field}' is not an exact copy of its pieces.", ids, record.id)

    missing = [p.id for p in pieces if p.id not in used]
    if missing:
        fail(f"{len(missing)} piece(s) are neither in the output nor in the removed lines: {', '.join(missing[:10])}", missing)
    return issues
