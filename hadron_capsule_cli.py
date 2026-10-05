"""Hadron v3.0 capsule create/verify/inspect CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_capsule import create_capsule_from_database, inspect_capsule
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Create and verify portable Hadron study capsules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a capsule from a saved study.")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--study-id", type=int, required=True)
    create.add_argument("--output", type=Path, required=True)

    verify = sub.add_parser("verify", help="Verify a capsule.")
    verify.add_argument("capsule", type=Path)
    verify.add_argument("--output", type=Path, default=None)

    inspect = sub.add_parser("inspect", help="Print capsule metadata.")
    inspect.add_argument("capsule", type=Path)

    args = parser.parse_args()
    print(f"HadronCapsule v{__version__}")

    if args.command == "create":
        result = create_capsule_from_database(
            args.db,
            args.study_id,
            args.output,
        )
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["verification"]["ok"] else 2)

    report = inspect_capsule(args.capsule)
    if args.command == "inspect":
        print(json.dumps(report["manifest"], indent=2))
        print(f"Capsule SHA-256: {report['capsule_sha256']}")
        print(f"Integrity: {'VERIFIED' if report['ok'] else 'FAILED'}")
        raise SystemExit(0 if report["ok"] else 2)

    print(json.dumps(report, indent=2))
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
