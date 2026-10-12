"""System instructions, kept as markdown files so they can be read and versioned."""

from pathlib import Path

_DIR = Path(__file__).parent


def load(*names: str) -> str:
    return "\n\n".join((_DIR / f"{name}.md").read_text(encoding="utf-8").strip() for name in names)
