"""Write records to files."""

import csv
import json
from pathlib import Path

from .core.assemble import Record
from .core.spec import Spec


def write_csv(records: list[Record], spec: Spec, path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(spec.columns)
        for r in records:
            writer.writerow([r.id] + [r.fields.get(name, "") for name in spec.columns[1:]])


def write_json(records: list[Record], spec: Spec, path: Path) -> None:
    rows = [{spec.id_column: r.id, **r.fields, "_pieces": r.sources} for r in records]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
