"""Run the whole pipeline on a test book with one or more models, and score each run.

Usage: python -m eval.bakeoff --book examples/mandala-brahmana --models openai/gpt-6-luna[,other/model]
"""

import argparse
import asyncio
import subprocess
from pathlib import Path

from agent.agents.models import ModelConfig
from agent.jobs import Job
from agent.orchestrator import RunConfig, run_job

from .score import print_report, read_rows, score_files

RUNS = Path("eval/runs")


def prompt_version() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h", "--", "agent/prompts"], capture_output=True, text=True, check=True).stdout.strip() or "uncommitted"
    except Exception:
        return "unknown"


async def run_one(book: Path, config: RunConfig, label: str, quiet: bool = False, spec: Path | None = None) -> dict:
    examples = book / "example_rows.csv"
    job = Job.create(
        (book / "input.md").read_text(encoding="utf-8"), (book / "request.txt").read_text(encoding="utf-8"),
        examples=examples.read_text(encoding="utf-8") if examples.exists() else "",
        name=f"{book.name}-{label}", root=RUNS,
    )
    if spec:
        job.write("spec.json", spec.read_text(encoding="utf-8"))
    summary = await run_job(job, config, say=(lambda message: None) if quiet else print)
    report = score_files(job.path("output.csv"), book) if job.has("output.csv") else {"exact_pct": 0, "exact_rows": 0, "gold_rows": 0, "mismatches": []}
    result = {"book": book.name, "label": label, "job": str(job.dir), "prompts": prompt_version(), "summary": summary, "score": report}
    job.save_json("eval.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--models", default="openai/gpt-6-luna", help="Comma-separated OpenRouter model IDs; each is used for all three agents.")
    parser.add_argument("--analyst-model", help="Use this model for the Analyst in every run.")
    parser.add_argument("--reviewer-model", help="Use this model for the Reviewer in every run.")
    parser.add_argument("--escalation-model")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--chunk-pages", type=int, default=6)
    parser.add_argument("--spec", type=Path, help="Reuse a Spec and skip the Analyst (isolates the Labeler).")
    parser.add_argument("--repeat", type=int, default=1, help="Run each model this many times, to see how much results vary.")
    args = parser.parse_args()
    results = asyncio.run(run_all(args))  # one event loop for every run: model clients are tied to their loop
    if len(results) > 1:
        print_agreement(results)

    print("\n| model | exact rows | errors | warnings | flagged | seconds | cost |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        s, sc = r["summary"], r["score"]
        print(f"| {r['label']} | {sc['exact_rows']}/{sc['gold_rows']} ({sc['exact_pct']}%) | {s.get('errors', '-')} | {s.get('warnings', '-')} | "
              f"{s.get('flagged_pieces', '-')} | {s.get('seconds', '-')} | ${s.get('cost_usd', '-')} |")


def print_agreement(results: list[dict]) -> None:
    """How many rows come out identical in every run. Covers the whole book, including rows with no expected output."""
    outputs = [read_rows(Path(r["job"]) / "output.csv")[1] for r in results if (Path(r["job"]) / "output.csv").exists()]
    ids = sorted(set().union(*outputs), key=lambda s: [int(x) for x in s.split(".")])
    differing = [i for i in ids if any(o.get(i) != outputs[0].get(i) for o in outputs[1:])]
    print(f"\nRows identical in all {len(outputs)} runs: {len(ids) - len(differing)}/{len(ids)}." + (f" Rows that differ: {', '.join(differing)}" if differing else ""))


async def run_all(args) -> list[dict]:
    results = []
    for model in [m for m in args.models.split(",") for _ in range(args.repeat)]:
        config = RunConfig(
            models=ModelConfig(analyst=args.analyst_model or model, labeler=model, reviewer=args.reviewer_model or model,
                               escalation=args.escalation_model, effort=args.effort),
            chunk_pages=args.chunk_pages, auto_approve=True,
        )
        print(f"\n=== {model} on {args.book.name} ===", flush=True)
        result = await run_one(args.book, config, model.split("/")[-1], spec=args.spec)
        print_report(result["score"])
        results.append(result)
    return results


if __name__ == "__main__":
    main()
