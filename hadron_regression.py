"""Multi-capsule reproducibility regression engine for Hadron v1.9."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from hadron_db import connect_db
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hadron_audit import append_audit
from hadron_capsule import inspect_capsule
from hadron_reproduce import reproduce_capsule
from hadron_version import (
    REGRESSION_BASELINE_VERSION,
    REGRESSION_REPORT_VERSION,
    __version__,
)


def discover_capsules(paths: Sequence[str | Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for capsule in path.rglob("*.hadron-capsule.zip"):
                found[str(capsule.resolve())] = capsule.resolve()
        elif path.is_file():
            found[str(path.resolve())] = path.resolve()
    return [found[key] for key in sorted(found)]


def _failure_fields(reproduction: Mapping[str, Any] | None) -> list[str]:
    if not reproduction:
        return []
    fields = []
    for check in reproduction.get("checks", []):
        if check.get("matching"):
            continue
        for difference in check.get("differences", []):
            fields.append(str(difference.get("field", "unknown")))
    return sorted(set(fields))


def run_capsule_regression(
    paths: Sequence[str | Path],
    *,
    workers_override: int | None = None,
    stop_on_failure: bool = False,
) -> dict[str, Any]:
    capsules = discover_capsules(paths)
    rows = []

    for index, capsule in enumerate(capsules):
        entry: dict[str, Any] = {
            "index": index,
            "capsule_path": str(capsule),
            "capsule_name": capsule.name,
            "status": "INVALID",
            "ok": False,
        }

        try:
            integrity = inspect_capsule(capsule)
            entry["capsule_sha256"] = integrity["capsule_sha256"]
            entry["integrity_ok"] = bool(integrity["ok"])
            entry["study_id"] = integrity["manifest"].get("study_id")
            entry["study_name"] = integrity["manifest"].get("study_name")

            if not integrity["ok"]:
                entry["status"] = "INVALID"
                entry["error"] = "Capsule integrity verification failed."
            else:
                reproduction = reproduce_capsule(
                    capsule,
                    workers_override=workers_override,
                )
                entry["reproduction"] = reproduction
                entry["jobs_compared"] = reproduction["jobs_compared"]
                entry["jobs_matching"] = reproduction["jobs_matching"]
                entry["failure_fields"] = _failure_fields(reproduction)
                entry["ok"] = bool(reproduction["exact_reproduction"])
                entry["status"] = (
                    "PASS" if reproduction["exact_reproduction"] else "DIVERGENCE"
                )

        except Exception as exc:
            entry["status"] = "ERROR"
            entry["error"] = f"{exc.__class__.__name__}: {exc}"

        rows.append(entry)

        if stop_on_failure and not entry["ok"]:
            break

    status_counts = Counter(row["status"] for row in rows)
    failure_field_counts = Counter(
        field
        for row in rows
        for field in row.get("failure_fields", [])
    )

    report = {
        "format": "hadron-regression-report",
        "format_version": REGRESSION_REPORT_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "capsules_discovered": len(capsules),
        "capsules_checked": len(rows),
        "passed": status_counts.get("PASS", 0),
        "diverged": status_counts.get("DIVERGENCE", 0),
        "invalid": status_counts.get("INVALID", 0),
        "errors": status_counts.get("ERROR", 0),
        "failure_field_counts": dict(sorted(failure_field_counts.items())),
        "all_passed": bool(rows) and all(row["ok"] for row in rows),
        "rows": rows,
    }
    return report


def create_regression_baseline(
    paths: Sequence[str | Path],
) -> dict[str, Any]:
    capsules = discover_capsules(paths)
    entries = []

    for capsule in capsules:
        verification = inspect_capsule(capsule)
        if not verification["ok"]:
            raise ValueError(f"Cannot baseline invalid capsule: {capsule}")
        manifest = verification["manifest"]
        entries.append(
            {
                "capsule_name": capsule.name,
                "capsule_sha256": verification["capsule_sha256"],
                "study_id": manifest.get("study_id"),
                "study_name": manifest.get("study_name"),
                "study_spec_sha256": manifest.get("study_spec_sha256"),
                "result_payload_sha256": manifest.get("result_payload_sha256"),
            }
        )

    return {
        "format": "hadron-regression-baseline",
        "format_version": REGRESSION_BASELINE_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "capsules": entries,
    }


def check_regression_baseline(
    baseline: Mapping[str, Any],
    paths: Sequence[str | Path],
    *,
    workers_override: int | None = None,
) -> dict[str, Any]:
    if baseline.get("format") != "hadron-regression-baseline":
        raise ValueError("Not a Hadron regression baseline.")
    if int(baseline.get("format_version", 0)) != REGRESSION_BASELINE_VERSION:
        raise ValueError("Unsupported regression baseline version.")

    discovered = discover_capsules(paths)
    by_hash = {}
    for capsule in discovered:
        verification = inspect_capsule(capsule)
        by_hash[verification["capsule_sha256"]] = (capsule, verification)

    rows = []
    for expected in baseline.get("capsules", []):
        expected_hash = str(expected.get("capsule_sha256", ""))
        entry = {
            "capsule_name": expected.get("capsule_name"),
            "expected_capsule_sha256": expected_hash,
            "study_name": expected.get("study_name"),
            "present": expected_hash in by_hash,
            "status": "MISSING",
            "ok": False,
        }

        if expected_hash in by_hash:
            capsule, verification = by_hash[expected_hash]
            entry["capsule_path"] = str(capsule)
            entry["integrity_ok"] = bool(verification["ok"])
            if verification["ok"]:
                reproduction = reproduce_capsule(
                    capsule,
                    workers_override=workers_override,
                )
                entry["reproduction"] = reproduction
                entry["failure_fields"] = _failure_fields(reproduction)
                entry["ok"] = bool(reproduction["exact_reproduction"])
                entry["status"] = (
                    "PASS" if reproduction["exact_reproduction"] else "DIVERGENCE"
                )
            else:
                entry["status"] = "INVALID"

        rows.append(entry)

    return {
        "format": "hadron-regression-baseline-check",
        "format_version": REGRESSION_REPORT_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_created_at": baseline.get("created_at"),
        "expected_capsules": len(baseline.get("capsules", [])),
        "capsules_found": sum(1 for row in rows if row["present"]),
        "passed": sum(1 for row in rows if row["status"] == "PASS"),
        "missing": sum(1 for row in rows if row["status"] == "MISSING"),
        "diverged": sum(1 for row in rows if row["status"] == "DIVERGENCE"),
        "invalid": sum(1 for row in rows if row["status"] == "INVALID"),
        "all_passed": bool(rows) and all(row["ok"] for row in rows),
        "rows": rows,
    }


def report_to_csv(report: Mapping[str, Any]) -> str:
    buffer = io.StringIO()
    fields = [
        "capsule_name",
        "study_name",
        "status",
        "ok",
        "capsule_sha256",
        "jobs_compared",
        "jobs_matching",
        "failure_fields",
        "error",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()

    for row in report.get("rows", []):
        reproduction = row.get("reproduction") or {}
        writer.writerow(
            {
                "capsule_name": row.get("capsule_name", ""),
                "study_name": row.get("study_name", ""),
                "status": row.get("status", ""),
                "ok": row.get("ok", False),
                "capsule_sha256": (
                    row.get("capsule_sha256")
                    or row.get("expected_capsule_sha256")
                    or ""
                ),
                "jobs_compared": (
                    row.get("jobs_compared")
                    or reproduction.get("jobs_compared")
                    or 0
                ),
                "jobs_matching": (
                    row.get("jobs_matching")
                    or reproduction.get("jobs_matching")
                    or 0
                ),
                "failure_fields": ",".join(row.get("failure_fields", [])),
                "error": row.get("error", ""),
            }
        )
    return buffer.getvalue()


def report_to_junit(report: Mapping[str, Any]) -> str:
    rows = list(report.get("rows", []))
    failures = sum(1 for row in rows if not row.get("ok"))

    suite = ET.Element(
        "testsuite",
        {
            "name": "HadronCapsuleRegression",
            "tests": str(len(rows)),
            "failures": str(failures),
            "errors": "0",
            "timestamp": str(report.get("created_at", "")),
        },
    )

    for row in rows:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "hadron.capsule",
                "name": str(row.get("capsule_name", "capsule")),
            },
        )
        if not row.get("ok"):
            failure = ET.SubElement(
                case,
                "failure",
                {
                    "message": str(row.get("status", "FAILED")),
                    "type": "HadronRegressionFailure",
                },
            )
            details = {
                "status": row.get("status"),
                "study_name": row.get("study_name"),
                "failure_fields": row.get("failure_fields", []),
                "error": row.get("error"),
            }
            failure.text = json.dumps(details, indent=2)

    return ET.tostring(suite, encoding="unicode")


def write_regression_outputs(
    report: Mapping[str, Any],
    *,
    json_path: str | Path | None = None,
    csv_path: str | Path | None = None,
    junit_path: str | Path | None = None,
) -> None:
    if json_path is not None:
        Path(json_path).write_text(
            json.dumps(dict(report), indent=2),
            encoding="utf-8",
        )
    if csv_path is not None:
        Path(csv_path).write_text(report_to_csv(report), encoding="utf-8")
    if junit_path is not None:
        Path(junit_path).write_text(report_to_junit(report), encoding="utf-8")


def record_regression_run(
    db_path: str | Path,
    report: Mapping[str, Any],
    *,
    mode: str = "run",
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO regression_runs(
                created_at, mode, capsules_checked, passed, failed,
                all_passed, report_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                str(mode),
                int(report.get("capsules_checked", report.get("expected_capsules", 0))),
                int(report.get("passed", 0)),
                int(
                    report.get("diverged", 0)
                    + report.get("invalid", 0)
                    + report.get("errors", 0)
                    + report.get("missing", 0)
                ),
                1 if report.get("all_passed") else 0,
                json.dumps(dict(report)),
            ),
        )
        run_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="regression",
        action=mode,
        entity_type="regression_run",
        entity_id=run_id,
        details={
            "all_passed": bool(report.get("all_passed")),
            "passed": int(report.get("passed", 0)),
        },
    )
    return run_id


def list_regression_runs(
    db_path: str | Path,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, mode, capsules_checked, passed, failed,
                   all_passed, report_json
            FROM regression_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "created_at": row[1],
            "mode": row[2],
            "capsules_checked": row[3],
            "passed": row[4],
            "failed": row[5],
            "all_passed": bool(row[6]),
            "report": json.loads(row[7]),
        }
        for row in rows
    ]


__all__ = [
    "discover_capsules",
    "run_capsule_regression",
    "create_regression_baseline",
    "check_regression_baseline",
    "report_to_csv",
    "report_to_junit",
    "write_regression_outputs",
    "record_regression_run",
    "list_regression_runs",
]
