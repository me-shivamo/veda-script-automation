"""Score an output CSV against a book's expected rows.

Usage: python -m eval.score <output.csv> <examples/book-dir>
"""

import csv
import json
import re
import sys
from pathlib import Path


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def read_rows(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        return columns, {row[columns[0]]: {c: normalize(row[c]) for c in columns[1:]} for row in reader}


def load_gold(book_dir: Path) -> tuple[list[str], dict[str, dict[str, str]], bool]:
    """Expected rows with the book's documented corrections applied. Returns (columns, rows, is_partial)."""
    full, partial = book_dir / "expected.csv", book_dir / "expected.partial.csv"
    path = full if full.exists() else partial
    columns, rows = read_rows(path)
    fixes = book_dir / "gold_fixes.json"
    if fixes.exists():
        for fix in json.loads(fixes.read_text(encoding="utf-8")):
            text, field = normalize(fix["text"]), fix["field"]
            src = rows[fix["remove_from"]]
            assert src[field].startswith(text), f"gold fix does not match row {fix['remove_from']}"
            src[field] = normalize(src[field][len(text):])
            if fix.get("add_to"):
                rows[fix["add_to"]][field] = normalize(text + " " + rows[fix["add_to"]][field])
    return columns, rows, path == partial


def first_difference(a: str, b: str, width: int = 30) -> str:
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"at char {i}: got …{a[max(i - width, 0):i + width]}… | expected …{b[max(i - width, 0):i + width]}…"


def score(actual: dict[str, dict[str, str]], gold: dict[str, dict[str, str]], fields: list[str]) -> dict:
    mismatches, exact_rows = [], 0
    field_ok = {f: 0 for f in fields}
    for num, want in gold.items():
        got = actual.get(num)
        if got is None:
            mismatches.append({"num": num, "field": "*", "detail": "row missing from output"})
            continue
        row_ok = True
        for f in fields:
            if got.get(f, "") == want[f]:
                field_ok[f] += 1
            else:
                row_ok = False
                mismatches.append({"num": num, "field": f, "detail": first_difference(got.get(f, ""), want[f])})
        exact_rows += row_ok
    return {
        "gold_rows": len(gold),
        "exact_rows": exact_rows,
        "exact_pct": round(100 * exact_rows / max(len(gold), 1), 1),
        "field_exact": field_ok,
        "missing_rows": sum(1 for m in mismatches if m["field"] == "*"),
        "output_rows": len(actual),
        "mismatches": mismatches,
    }


def score_files(output_csv: Path, book_dir: Path) -> dict:
    columns, gold, partial = load_gold(book_dir)
    _, actual = read_rows(output_csv)
    report = score(actual, gold, columns[1:])
    report["gold_is_partial"] = partial
    return report


def print_report(report: dict) -> None:
    note = " (expected file is partial; later rows are unscored)" if report.get("gold_is_partial") else ""
    print(f"Exact rows: {report['exact_rows']}/{report['gold_rows']} = {report['exact_pct']}%{note}")
    print("Per column:", ", ".join(f"{f} {n}/{report['gold_rows']}" for f, n in report["field_exact"].items()))
    print(f"Output has {report['output_rows']} rows; {report['missing_rows']} expected rows are missing.")
    for m in report["mismatches"]:
        print(f"  {m['num']} [{m['field']}] {m['detail']}")


if __name__ == "__main__":
    result = score_files(Path(sys.argv[1]), Path(sys.argv[2]))
    print_report(result)
    sys.exit(0 if not result["mismatches"] else 1)
