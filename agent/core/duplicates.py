"""Find pages that the OCR produced twice."""

import re

from .pieces import Piece

_NOISE = re.compile(r"[\s\-—–.,;:'\"“”‘’()\[\]#•|]+")


def _shingles(text: str, size: int = 6) -> set[str]:
    text = _NOISE.sub("", text)
    return {text[i:i + size] for i in range(max(len(text) - size + 1, 0))}


def find_duplicate_pages(pieces: list[Piece], threshold: float = 0.6, min_shingles: int = 150) -> list[tuple[int, int, float]]:
    """Return (earlier_page, later_page, overlap) for pages that repeat an earlier page.

    Overlap is the share of the smaller page that also appears in the other one,
    so a page that was only partly repeated is still found.
    """
    by_page: dict[int, list[str]] = {}
    for p in pieces:
        by_page.setdefault(p.page, []).append(p.text)
    sets = {page: _shingles(" ".join(texts)) for page, texts in by_page.items()}
    sets = {page: s for page, s in sets.items() if len(s) >= min_shingles}
    found, pages = [], sorted(sets)
    for i, a in enumerate(pages):
        for b in pages[i + 1:]:
            overlap = len(sets[a] & sets[b]) / min(len(sets[a]), len(sets[b]))
            if overlap >= threshold:
                found.append((a, b, round(overlap, 2)))
    return found
