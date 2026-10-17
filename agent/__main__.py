"""Command line: python -m agent run INPUT --request "what to extract" [options]"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from .agents.analyst import AnalystResult
from .agents.models import DEFAULT_MODEL, ModelConfig
from .jobs import JOBS_DIR, Job
from .orchestrator import RunConfig, run_job
from .plan import describe_plan


def _text_or_file(value: str) -> str:
    path = Path(value)
    return path.read_text(encoding="utf-8") if len(value) < 300 and path.is_file() else value


async def _ask_user(result: AnalystResult, view: dict) -> bool | str:
    print("\n" + "=" * 78 + "\n" + describe_plan(view) + "\n" + "=" * 78)
    answer = (await asyncio.to_thread(input, "\nApprove? [y] yes  [n] stop  or type what to change: ")).strip()
    if answer.lower() in ("y", "yes", ""):
        return True
    if answer.lower() in ("n", "no"):
        return False
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agent", description="Structure OCR text of a Sanskrit book into one row per shloka.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the agents on one book.")
    run.add_argument("input", type=Path, help="OCR text file (pages separated by lines of ---).")
    run.add_argument("--request", required=True, help="What to extract and in what format: text, or a path to a text file.")
    run.add_argument("--examples", type=Path, help="CSV with a few rows of the output you want (optional but helps a lot).")
    run.add_argument("--spec", type=Path, help="Reuse a saved Spec (spec.json) and skip the Analyst.")
    run.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model for all agents.")
    run.add_argument("--analyst-model")
    run.add_argument("--labeler-model")
    run.add_argument("--reviewer-model")
    run.add_argument("--escalation-model", help="Stronger model for chunks the Labeler cannot finish.")
    run.add_argument("--effort", default="medium", choices=["minimal", "low", "medium", "high", "xhigh"])
    run.add_argument("--chunk-pages", type=int, default=6)
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--pages", help="Only process this page range, e.g. 1-10.")
    run.add_argument("--auto-approve", action="store_true", help="Do not stop to ask for approval of the Spec.")
    run.add_argument("--jobs-dir", type=Path, default=JOBS_DIR)
    args = parser.parse_args()

    job = Job.create(
        args.input.read_text(encoding="utf-8"), _text_or_file(args.request),
        examples=args.examples.read_text(encoding="utf-8") if args.examples else "",
        name=args.input.parent.name if args.input.stem == "input" else args.input.stem, root=args.jobs_dir,
    )
    if args.spec:
        shutil.copy(args.spec, job.path("spec.json"))
    config = RunConfig(
        models=ModelConfig(
            analyst=args.analyst_model or args.model, labeler=args.labeler_model or args.model,
            reviewer=args.reviewer_model or args.model, escalation=args.escalation_model, effort=args.effort,
        ),
        chunk_pages=args.chunk_pages, concurrency=args.concurrency, auto_approve=args.auto_approve,
        pages=tuple(int(x) for x in args.pages.split("-")) if args.pages else None,
    )
    print(f"Job folder: {job.dir}")
    summary = asyncio.run(run_job(job, config, approve=_ask_user))
    return 0 if summary["state"] == "DONE" else 1


if __name__ == "__main__":
    sys.exit(main())
