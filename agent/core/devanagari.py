"""Devanagari digits and verse-number markers."""

import re
import unicodedata

DEV_DIGITS = "०१२३४५६७८९"
_TO_ASCII = str.maketrans(DEV_DIGITS, "0123456789")

NUM = r"[०-९0-9]+"
DANDA2 = r"(?:॥|।।)"

# A numbered verse marker: ॥ ५ ॥, ॥५॥, ॥ १२-१३ ॥, ।। ५ ।।
VERSE_MARKER = re.compile(rf"{DANDA2}\s*({NUM})(?:\s*[-–]\s*({NUM}))?\s*{DANDA2}")


def to_int(digits: str) -> int:
    return int(digits.translate(_TO_ASCII))


def marker_numbers(match: re.Match) -> list[int]:
    first = to_int(match.group(1))
    if match.group(2) is None:
        return [first]
    last = to_int(match.group(2))
    return list(range(first, last + 1)) if first <= last <= first + 20 else [first, last]


def other_script(text: str) -> bool:
    """True if most letters are neither Devanagari nor Latin, e.g. a Tamil or Telugu line the OCR picked up."""
    letters = [ch for ch in text if ch.isalpha() or unicodedata.category(ch).startswith("M")]
    if len(letters) < 4:
        return False
    usual = sum(1 for ch in letters if "ऀ" <= ch <= "ॿ" or "꣠" <= ch <= "ꣿ" or ch.isascii() or "᳐" <= ch <= "᳿")
    return usual / len(letters) < 0.5
