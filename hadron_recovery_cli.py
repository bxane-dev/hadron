"""Hadron v3.0 recovery snapshot CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_recovery import (
    create_recovery_snapshot,
    inspect_recovery_snapshot,
    restore_recovery_snapshot,
)
from hadron_storage import resolve_data_dir
from hadron_version import __version__


def _paths(data_dir: Path):
    return (
        data_dir / "hadron_runs.db",
        data_dir / "settings.json",
        data_dir / "session.json",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Create, inspect, and restore Hadron recovery snapshots."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--data-dir", type=Path, default=None)
    create.add_argument("--output", type=Path, required=True)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("snapshot", type=Path)

    restore = sub.add_parser("restore")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--data-dir", type=Path, default=None)

    args = parser.parse_args()
    print(f"HadronRecovery v{__version__}")

    if args.command == "inspect":
        report = inspect_recovery_snapshot(args.snapshot)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["ok"] else 2)

    data_dir = (
        args.data_dir.resolve()
        if args.data_dir
        else resolve_data_dir()
    )
    db_path, settings_path, session_path = _paths(data_dir)

    if args.command == "create":
        path = create_recovery_snapshot(
            args.output,
            data_dir=data_dir,
            db_path=db_path,
            settings_path=settings_path,
            session_path=session_path,
        )
        print(path)
        return

    result = restore_recovery_snapshot(
        args.snapshot,
        data_dir=data_dir,
        db_path=db_path,
        settings_path=settings_path,
        session_path=session_path,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
