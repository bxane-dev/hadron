"""Executable capsule reproducibility verification for Hadron v1.8."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from hadron_db import connect_db
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_audit import append_audit
from hadron_capsule import inspect_capsule
from hadron_jobs import run_job_spec
from hadron_studies import create_study, normalize_study_spec, set_study_status
from hadron_version import REPRODUCTION_REPORT_VERSION, __version__


NONDETERMINISTIC_JOB_FIELDS = {
    "created_at",
}


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_job_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic portion of one job result."""
    return {
        key: value
        for key, value in dict(row).items()
        if key not in NONDETERMINISTIC_JOB_FIELDS
    }


def stable_job_hash(row: Mapping[str, Any]) -> str:
    return _canonical_hash(stable_job_payload(row))


def load_capsule_study(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path)
    verification = inspect_capsule(path)
    if not verification["ok"]:
        raise ValueError("Capsule integrity verification failed.")

    with zipfile.ZipFile(path) as z:
        study = json.loads(z.read("study.json").decode("utf-8"))

    if not isinstance(study, dict):
        raise ValueError("Capsule study payload is invalid.")
    if not study.get("result"):
        raise ValueError("Capsule does not contain completed study results.")

    return study, verification


def _field_diff(
    original: Mapping[str, Any],
    reproduced: Mapping[str, Any],
) -> list[dict[str, Any]]:
    left = stable_job_payload(original)
    right = stable_job_payload(reproduced)
    keys = sorted(set(left) | set(right))
    return [
        {
            "field": key,
            "original": left.get(key),
            "reproduced": right.get(key),
        }
        for key in keys
        if left.get(key) != right.get(key)
    ]


def reproduce_capsule(
    capsule_path: str | Path,
    *,
    workers_override: int | None = None,
) -> dict[str, Any]:
    study, verification = load_capsule_study(capsule_path)
    spec = normalize_study_spec(study.get("spec") or {})

    workers = (
        int(workers_override)
        if workers_override is not None
        else int(spec.get("workers", 1))
    )
    if not 1 <= workers <= 8:
        raise ValueError("workers_override must be between 1 and 8.")

    reproduced = run_job_spec(spec, workers=workers)
    original_rows = list((study.get("result") or {}).get("results") or [])
    reproduced_rows = list(reproduced.get("results") or [])

    original_by_index = {
        int(row.get("job_index", index)): row
        for index, row in enumerate(original_rows)
    }
    reproduced_by_index = {
        int(row.get("job_index", index)): row
        for index, row in enumerate(reproduced_rows)
    }

    job_indexes = sorted(set(original_by_index) | set(reproduced_by_index))
    checks = []
    matched = 0

    for job_index in job_indexes:
        original = original_by_index.get(job_index)
        rerun = reproduced_by_index.get(job_index)

        if original is None or rerun is None:
            checks.append(
                {
                    "job_index": job_index,
                    "original_present": original is not None,
                    "reproduced_present": rerun is not None,
                    "original_hash": (
                        stable_job_hash(original) if original is not None else None
                    ),
                    "reproduced_hash": (
                        stable_job_hash(rerun) if rerun is not None else None
                    ),
                    "matching": False,
                    "differences": [
                        {
                            "field": "__job_presence__",
                            "original": original is not None,
                            "reproduced": rerun is not None,
                        }
                    ],
                }
            )
            continue

        original_hash = stable_job_hash(original)
        reproduced_hash = stable_job_hash(rerun)
        matching = original_hash == reproduced_hash
        if matching:
            matched += 1

        checks.append(
            {
                "job_index": job_index,
                "original_present": True,
                "reproduced_present": True,
                "original_hash": original_hash,
                "reproduced_hash": reproduced_hash,
                "matching": matching,
                "differences": [] if matching else _field_diff(original, rerun),
            }
        )

    report = {
        "format": "hadron-reproduction-report",
        "format_version": REPRODUCTION_REPORT_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "capsule_path": str(Path(capsule_path)),
        "capsule_sha256": verification["capsule_sha256"],
        "capsule_integrity_ok": verification["ok"],
        "source_study_id": study.get("id"),
        "source_study_name": study.get("name"),
        "source_result_version": (study.get("result") or {}).get("version"),
        "rerun_version": reproduced.get("version"),
        "workers_used": workers,
        "jobs_original": len(original_rows),
        "jobs_reproduced": len(reproduced_rows),
        "jobs_compared": len(checks),
        "jobs_matching": matched,
        "jobs_different": len(checks) - matched,
        "exact_reproduction": bool(
            verification["ok"]
            and len(original_rows) == len(reproduced_rows)
            and checks
            and matched == len(checks)
        ),
        "checks": checks,
        "rerun_result": reproduced,
    }
    return report


def record_reproduction_check(
    db_path: str | Path,
    report: Mapping[str, Any],
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO reproduction_checks(
                created_at, capsule_sha256, source_study_id,
                source_study_name, jobs_compared, jobs_matching,
                exact_reproduction, report_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                report.get("capsule_sha256"),
                report.get("source_study_id"),
                report.get("source_study_name"),
                int(report.get("jobs_compared", 0)),
                int(report.get("jobs_matching", 0)),
                1 if report.get("exact_reproduction") else 0,
                json.dumps(dict(report)),
            ),
        )
        check_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="reproduction",
        action="verify",
        entity_type="reproduction_check",
        entity_id=check_id,
        details={
            "capsule_sha256": report.get("capsule_sha256"),
            "source_study_id": report.get("source_study_id"),
            "jobs_compared": report.get("jobs_compared"),
            "jobs_matching": report.get("jobs_matching"),
            "exact_reproduction": bool(report.get("exact_reproduction")),
        },
    )
    return check_id


def reproduce_capsule_to_database(
    db_path: str | Path,
    capsule_path: str | Path,
    *,
    workers_override: int | None = None,
) -> dict[str, Any]:
    report = reproduce_capsule(
        capsule_path,
        workers_override=workers_override,
    )
    report["database_check_id"] = record_reproduction_check(db_path, report)
    return report


def restore_capsule_study(
    db_path: str | Path,
    capsule_path: str | Path,
    *,
    name: str | None = None,
) -> int:
    study, verification = load_capsule_study(capsule_path)
    if not verification["ok"]:
        raise ValueError("Capsule is not verified.")

    restored_name = (
        str(name).strip()
        if name is not None and str(name).strip()
        else f"{study.get('name', 'Capsule Study')} (restored)"
    )
    study_id = create_study(
        db_path,
        name=restored_name,
        spec=study.get("spec") or {},
    )
    set_study_status(
        db_path,
        study_id,
        "COMPLETE",
        result=study.get("result") or {},
    )

    append_audit(
        db_path,
        category="capsule",
        action="restore-study",
        entity_type="study",
        entity_id=study_id,
        details={
            "capsule_sha256": verification["capsule_sha256"],
            "source_study_id": study.get("id"),
            "source_study_name": study.get("name"),
        },
    )
    return study_id


def list_reproduction_checks(
    db_path: str | Path,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, capsule_sha256, source_study_id,
                   source_study_name, jobs_compared, jobs_matching,
                   exact_reproduction, report_json
            FROM reproduction_checks
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
            "capsule_sha256": row[2],
            "source_study_id": row[3],
            "source_study_name": row[4],
            "jobs_compared": row[5],
            "jobs_matching": row[6],
            "exact_reproduction": bool(row[7]),
            "report": json.loads(row[8]),
        }
        for row in rows
    ]


__all__ = [
    "stable_job_payload",
    "stable_job_hash",
    "load_capsule_study",
    "reproduce_capsule",
    "record_reproduction_check",
    "reproduce_capsule_to_database",
    "restore_capsule_study",
    "list_reproduction_checks",
]
