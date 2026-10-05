"""Hadron v3.0 multi-capsule regression CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_regression import (
    check_regression_baseline,
    create_regression_baseline,
    record_regression_run,
    run_capsule_regression,
    write_regression_outputs,
)
from hadron_version import __version__


def _add_output_args(parser):
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--junit", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=None)


def main():
    parser = argparse.ArgumentParser(
        description="Run CI-friendly regression checks across Hadron capsules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("paths", nargs="+", type=Path)
    run.add_argument("--stop-on-failure", action="store_true")
    _add_output_args(run)

    baseline = sub.add_parser("baseline")
    baseline.add_argument("paths", nargs="+", type=Path)
    baseline.add_argument("--output", type=Path, required=True)

    check = sub.add_parser("check")
    check.add_argument("baseline", type=Path)
    check.add_argument("paths", nargs="+", type=Path)
    _add_output_args(check)

    args = parser.parse_args()
    print(f"HadronRegression v{__version__}")

    if args.command == "baseline":
        payload = create_regression_baseline(args.paths)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Baseline capsules: {len(payload['capsules'])}")
        print(f"Saved: {args.output}")
        return

    if args.command == "run":
        report = run_capsule_regression(
            args.paths,
            workers_override=args.workers,
            stop_on_failure=args.stop_on_failure,
        )
        mode = "run"
    else:
        baseline_payload = json.loads(
            args.baseline.read_text(encoding="utf-8")
        )
        report = check_regression_baseline(
            baseline_payload,
            args.paths,
            workers_override=args.workers,
        )
        mode = "baseline-check"

    write_regression_outputs(
        report,
        json_path=args.json,
        csv_path=args.csv,
        junit_path=args.junit,
    )

    if args.db is not None:
        report["database_run_id"] = record_regression_run(
            args.db,
            report,
            mode=mode,
        )
        if args.json is not None:
            args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"Passed: {report.get('passed', 0)} · "
        f"Failed: "
        f"{report.get('diverged', 0) + report.get('invalid', 0) + report.get('errors', 0) + report.get('missing', 0)}"
    )
    print("REGRESSION PASS" if report["all_passed"] else "REGRESSION FAIL")
    raise SystemExit(0 if report["all_passed"] else 4)


if __name__ == "__main__":
    main()
