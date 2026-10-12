"""One job = one folder holding everything about a run, so it can be inspected or resumed."""

import json
import re
import time
from pathlib import Path

JOBS_DIR = Path("jobs")


class Job:
    def __init__(self, directory: Path):
        self.dir = directory

    @classmethod
    def create(cls, input_text: str, request: str, examples: str = "", name: str = "book", root: Path = JOBS_DIR) -> "Job":
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "book"
        job = cls(root / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}")
        job.dir.mkdir(parents=True, exist_ok=False)
        job.write("input.md", input_text)
        job.write("request.txt", request)
        if examples:
            job.write("examples.csv", examples)
        return job

    def path(self, name: str) -> Path:
        return self.dir / name

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def write(self, name: str, text: str) -> None:
        self.path(name).write_text(text, encoding="utf-8")

    def read(self, name: str, default: str = "") -> str:
        return self.path(name).read_text(encoding="utf-8") if self.has(name) else default

    def save_json(self, name: str, data) -> None:
        self.write(name, json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    def load_json(self, name: str):
        return json.loads(self.read(name))
