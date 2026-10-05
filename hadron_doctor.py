"""Hadron v3.0 diagnostics/self-test command."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from hadron_engine import SimulationConfig, run_counts
from hadron_db import initialize_runtime_pragmas
from hadron_storage import migrate_database, resolve_data_dir
from hadron_system import database_diagnostics, environment_diagnostics
from hadron_version import SCHEMA_VERSION, __version__


def run_doctor(data_dir: Path, benchmark_events: int = 5000) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "hadron_runs.db"

    migration = migrate_database(db_path)
    runtime_pragmas = initialize_runtime_pragmas(db_path)
    db = database_diagnostics(db_path)

    config = SimulationConfig(
        events=250,
        energy=6500.0,
        preset="STANDARD",
        seed=100100,
    )
    first = run_counts(config)
    second = run_counts(config)

    benchmark_config = SimulationConfig(
        events=max(100, int(benchmark_events)),
        energy=6500.0,
        preset="STANDARD",
        seed=111222,
    )
    started = time.perf_counter()
    benchmark_counts = run_counts(benchmark_config)
    elapsed = max(time.perf_counter() - started, 1e-9)
    events_per_second = benchmark_config.events / elapsed

    write_test = data_dir / ".doctor-write-test"
    write_test.write_text("ok", encoding="utf-8")
    writable = write_test.read_text(encoding="utf-8") == "ok"
    write_test.unlink(missing_ok=True)

    checks = {
        "database_integrity": db.get("integrity") == "ok",
        "schema_current": db.get("schema_version") == SCHEMA_VERSION,
        "engine_deterministic": first == second,
        "data_dir_writable": writable,
        "foreign_keys_enabled": runtime_pragmas.get("foreign_keys") == "1",
        "busy_timeout_configured": (
            int(runtime_pragmas.get("busy_timeout_ms", "0")) >= 5000
        ),
        "wal_enabled": (
            runtime_pragmas.get("journal_mode", "").lower() == "wal"
        ),
    }

    return {
        "application": "Hadron",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "data_dir": str(data_dir),
        "environment": environment_diagnostics(),
        "database": db,
        "migration": migration,
        "runtime_pragmas": runtime_pragmas,
        "engine_smoke_counts": first,
        "benchmark": {
            "events": benchmark_config.events,
            "elapsed_seconds": elapsed,
            "events_per_second": events_per_second,
            "counts": benchmark_counts,
        },
        "checks": checks,
        "ok": all(checks.values()),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Hadron v3.0 local diagnostics and self-test."
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--benchmark-events",
        type=int,
        default=5000,
        help="Synthetic events used for the engine benchmark.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir.resolve() if args.data_dir else resolve_data_dir()
    report = run_doctor(data_dir, benchmark_events=args.benchmark_events)
    print(json.dumps(report, indent=2))

    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
