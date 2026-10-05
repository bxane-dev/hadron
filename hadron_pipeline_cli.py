"""Hadron v3.0 pipeline CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_pipeline import (
    create_pipeline,
    list_pipelines,
    pipeline_to_junit,
    run_pipeline_to_database,
)
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Run Hadron campaign pipelines."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--campaign-id", type=int, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--auto-reference", action="store_true")

    listing = sub.add_parser("list")
    listing.add_argument("--db", type=Path, required=True)

    run = sub.add_parser("run")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--pipeline-id", type=int, required=True)
    run.add_argument("--report-dir", type=Path, default=None)
    run.add_argument("--json", type=Path, default=None)
    run.add_argument("--junit", type=Path, default=None)

    args = parser.parse_args()
    print(f"HadronPipeline v{__version__}")

    if args.command == "create":
        stages = (
            [
                "health",
                "ensure-reference",
                "reference-check",
                "campaign-run",
                "report",
            ]
            if args.auto_reference
            else [
                "health",
                "reference-check",
                "campaign-run",
                "report",
            ]
        )
        pipeline_id = create_pipeline(
            args.db,
            name=args.name,
            campaign_id=args.campaign_id,
            spec={
                "stages": stages,
                "auto_create_reference": args.auto_reference,
            },
        )
        print(pipeline_id)
        return

    if args.command == "list":
        print(json.dumps(list_pipelines(args.db), indent=2))
        return

    report = run_pipeline_to_database(
        args.db,
        args.pipeline_id,
        report_dir=args.report_dir,
    )

    if args.json:
        args.json.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
    if args.junit:
        args.junit.write_text(
            pipeline_to_junit(report),
            encoding="utf-8",
        )

    print("PIPELINE PASS" if report["passed"] else "PIPELINE FAIL")
    raise SystemExit(0 if report["passed"] else 8)


if __name__ == "__main__":
    main()
