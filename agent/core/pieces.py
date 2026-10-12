"""Cut OCR text into small numbered pieces.

A piece is one OCR line, cut again right after every numbered verse marker
(॥ ५ ॥), so that two shlokas printed on one line become two pieces. Agents
label pieces; the output is assembled from piece text, never from model text.
"""

import re

from pydantic import BaseModel

from .devanagari import VERSE_MARKER, marker_numbers

PAGE_BREAK = re.compile(r"^\s*-{3,}\s*$")
_FOOTNOTE_DEF = re.compile(r"^\[\^\w+\]:")
_FOOTNOTE_LINE = re.compile(r"^['‘’\"]?\s*[0-9०-९]{1,2}[.)]?\s+\S")
_SIGLA = re.compile(r"[—–-]\s*[\"“]?(?:अ|उ|क|ख|ग)(?:\s?[१२३1-3])?(?=[\s.,]|$)")
_EDGE_NUMBER = re.compile(r"^[0-9०-९]+\b|[0-9०-९]+\s*$")
_DIGITS_ONLY = re.compile(r"^[0-9]{1,4}$")


class Piece(BaseModel):
    id: str
    page: int
    text: str
    starts_line: bool = True
    ends_line: bool = True
    blank_before: bool = False
    printed_n: list[int] = []
    hints: list[str] = []


def _hints(text: str, *, first_on_page: bool) -> list[str]:
    hints = []
    if text.startswith("#"):
        hints.append("md-heading")
    if _DIGITS_ONLY.match(text):
        hints.append("digits-only")
    if _FOOTNOTE_DEF.match(text) or (_FOOTNOTE_LINE.match(text) and _SIGLA.search(text)):
        hints.append("footnote-like")
    if first_on_page and len(text) <= 60 and "॥" not in text and _EDGE_NUMBER.search(text):
        hints.append("page-top-number")
    if len(text) <= 45 and "।" not in text and "॥" not in text:
        hints.append("short")
    return hints


def _printed_n(text: str) -> list[int]:
    last = None
    for last in VERSE_MARKER.finditer(text):
        pass
    if last is None or text[last.end():].strip(" .।"):
        return []
    return marker_numbers(last)


def _cut_line(line: str) -> list[str]:
    """Cut a line after each verse marker that has more text following it."""
    parts, start = [], 0
    for m in VERSE_MARKER.finditer(line):
        if line[m.end():].strip():
            parts.append(line[start:m.end()].strip())
            start = m.end()
    parts.append(line[start:].strip())
    return [p for p in parts if p]


def split_pages(text: str) -> list[list[str]]:
    pages, current = [], []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if PAGE_BREAK.match(line):
            pages.append(current)
            current = []
        else:
            current.append(line)
    pages.append(current)
    return [p for p in pages if any(line.strip() for line in p)]


def make_pieces(text: str) -> list[Piece]:
    pieces: list[Piece] = []
    for page_no, lines in enumerate(split_pages(text), start=1):
        blank, first_on_page = False, True
        for line in lines:
            if not line.strip():
                blank = True
                continue
            parts = _cut_line(line.strip())
            for i, part in enumerate(parts):
                pieces.append(Piece(
                    id=f"P{len(pieces) + 1:04d}",
                    page=page_no,
                    text=part,
                    starts_line=i == 0,
                    ends_line=i == len(parts) - 1,
                    blank_before=blank and i == 0,
                    printed_n=_printed_n(part),
                    hints=_hints(part, first_on_page=first_on_page and i == 0),
                ))
            blank, first_on_page = False, False
    return pieces


def split_piece(pieces: list[Piece], piece_id: str, after_text: str) -> tuple[Piece, Piece]:
    """Split one piece in two, right after the only occurrence of after_text.

    Replaces the piece in the list with two pieces named <id>a and <id>b.
    Raises ValueError when after_text is missing, repeated, or leaves an empty half.
    """
    index = next((i for i, p in enumerate(pieces) if p.id == piece_id), None)
    if index is None:
        raise ValueError(f"{piece_id} does not exist (it may already have been split)")
    piece = pieces[index]
    count = piece.text.count(after_text) if after_text else 0
    if count != 1:
        raise ValueError(f"after_text must occur exactly once in {piece_id}; it occurs {count} times")
    cut = piece.text.index(after_text) + len(after_text)
    left, right = piece.text[:cut].strip(), piece.text[cut:].strip()
    if not left or not right:
        raise ValueError("the split would leave one half empty")
    a = piece.model_copy(update={
        "id": piece.id + "a", "text": left, "ends_line": False,
        "printed_n": _printed_n(left), "hints": _hints(left, first_on_page=False),
    })
    b = piece.model_copy(update={
        "id": piece.id + "b", "text": right, "starts_line": False, "blank_before": False,
        "printed_n": _printed_n(right), "hints": _hints(right, first_on_page=False),
    })
    pieces[index:index + 1] = [a, b]
    return a, b


def page_numbers(pieces: list[Piece]) -> list[int]:
    return sorted({p.page for p in pieces})
