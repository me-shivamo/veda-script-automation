"""Devanagari digits and verse-number markers."""

import re

DEV_DIGITS = "०१२३४५६७८९"
_TO_ASCII = str.maketrans(DEV_DIGITS, "0123456789")
_TO_DEV = str.maketrans("0123456789", DEV_DIGITS)

NUM = r"[०-९0-9]+"
DANDA2 = r"(?:॥|।।)"

# A numbered verse marker: ॥ ५ ॥, ॥५॥, ॥ १२-१३ ॥, ।। ५ ।।
VERSE_MARKER = re.compile(rf"{DANDA2}\s*({NUM})(?:\s*[-–]\s*({NUM}))?\s*{DANDA2}")


def to_int(digits: str) -> int:
    return int(digits.translate(_TO_ASCII))


def to_dev(n: int) -> str:
    return str(n).translate(_TO_DEV)


def like_digits(n: int, sample: str) -> str:
    """Write n in the same digit script as sample."""
    return to_dev(n) if any(ch in DEV_DIGITS for ch in sample) else str(n)


def marker_numbers(match: re.Match) -> list[int]:
    first = to_int(match.group(1))
    if match.group(2) is None:
        return [first]
    last = to_int(match.group(2))
    return list(range(first, last + 1)) if first <= last <= first + 20 else [first, last]
