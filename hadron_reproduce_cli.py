"""Hadron v3.0 capsule reproduction CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_reproduce import (
    reproduce_capsule,
    reproduce_capsule_to_database,
    restore_capsule_study,
)
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Re-run, compare, and restore Hadron study capsules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rerun = sub.add_parser(
        "rerun",
        help="Re-run a capsule's exact study definition and compare results.",
    )
    rerun.add_argument("capsule", type=Path)
    rerun.add_argument("--workers", type=int, default=None)
    rerun.add_argument("--db", type=Path, default=None)
    rerun.add_argument("--output", type=Path, default=None)

    restore = sub.add_parser(
        "restore",
        help="Restore a verified capsule as a local completed study.",
    )
    restore.add_argument("capsule", type=Path)
    restore.add_argument("--db", type=Path, required=True)
    restore.add_argument("--name", default=None)

    args = parser.parse_args()
    print(f"HadronReproduce v{__version__}")

    if args.command == "restore":
        study_id = restore_capsule_study(
            args.db,
            args.capsule,
            name=args.name,
        )
        print(f"Restored as study #{study_id}.")
        return

    if args.db is not None:
        report = reproduce_capsule_to_database(
            args.db,
            args.capsule,
            workers_override=args.workers,
        )
    else:
        report = reproduce_capsule(
            args.capsule,
            workers_override=args.workers,
        )

    if args.output is not None:
        args.output.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )

    print(f"Jobs matching: {report['jobs_matching']}/{report['jobs_compared']}")
    print(
        "EXACT REPRODUCTION"
        if report["exact_reproduction"]
        else "DIVERGENCE DETECTED"
    )
    raise SystemExit(0 if report["exact_reproduction"] else 3)


if __name__ == "__main__":
    main()
