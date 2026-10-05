"""Persistent Study Queue helpers for Hadron v2.3."""

from __future__ import annotations

import json
import sqlite3
from hadron_db import connect_db
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_audit import append_audit
from hadron_engine import PRESETS


VALID_STUDY_STATUSES = {
    "QUEUED",
    "RUNNING",
    "COMPLETE",
    "FAILED",
    "INTERRUPTED",
}


def normalize_study_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    events = int(spec.get("events", 5000))
    repeats = int(spec.get("repeats", 1))
    seed = int(spec.get("seed", 42))
    workers = int(spec.get("workers", 1))
    l1_threshold = float(spec.get("l1_threshold", 5000.0))
    met_threshold = float(spec.get("met_threshold", 500.0))
    higgs_window = float(spec.get("higgs_window", 3.0))
    noise = bool(spec.get("noise", True))
    noise_sigma = (
        None
        if spec.get("noise_sigma") is None
        else float(spec.get("noise_sigma"))
    )
    resolution_sigma = (
        None
        if spec.get("resolution_sigma") is None
        else float(spec.get("resolution_sigma"))
    )

    energies_raw = spec.get("energies", [6500.0])
    if isinstance(energies_raw, (int, float)):
        energies_raw = [energies_raw]
    energies = [float(value) for value in energies_raw]

    presets_raw = spec.get("presets", [spec.get("preset", "STANDARD")])
    if isinstance(presets_raw, str):
        presets_raw = [presets_raw]
    presets = [str(value) for value in presets_raw]

    if not 1 <= events <= 1_000_000:
        raise ValueError("Study events must be between 1 and 1,000,000.")
    if not 1 <= repeats <= 100:
        raise ValueError("Study repeats must be between 1 and 100.")
    if not 1 <= workers <= 8:
        raise ValueError("Study workers must be between 1 and 8.")
    if not energies:
        raise ValueError("At least one beam energy is required.")
    if any(not 1000.0 <= energy <= 7000.0 for energy in energies):
        raise ValueError("Every beam energy must be between 1000 and 7000 GeV.")
    if not presets:
        raise ValueError("At least one physics preset is required.")
    unknown = [preset for preset in presets if preset not in PRESETS]
    if unknown:
        raise ValueError(f"Unknown physics preset(s): {', '.join(unknown)}")
    if l1_threshold < 0 or met_threshold < 0 or higgs_window <= 0:
        raise ValueError("Trigger thresholds/window are invalid.")
    if noise_sigma is not None and noise_sigma < 0:
        raise ValueError("Noise sigma must be >= 0.")
    if resolution_sigma is not None and resolution_sigma < 0:
        raise ValueError("Resolution sigma must be >= 0.")

    return {
        "events": events,
        "energies": energies,
        "presets": presets,
        "seed": seed,
        "repeats": repeats,
        "workers": workers,
        "l1_threshold": l1_threshold,
        "met_threshold": met_threshold,
        "higgs_window": higgs_window,
        "noise": noise,
        "noise_sigma": noise_sigma,
        "resolution_sigma": resolution_sigma,
    }


def create_study(
    db_path: str | Path,
    *,
    name: str,
    spec: Mapping[str, Any],
) -> int:
    normalized = normalize_study_spec(spec)
    now = datetime.now().isoformat(timespec="seconds")
    study_name = str(name).strip() or "Untitled Study"

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO study_jobs (
                name, created_at, updated_at, status, workers,
                spec_json, result_json, error_text
            )
            VALUES (?, ?, ?, 'QUEUED', ?, ?, NULL, '')
            """,
            (
                study_name,
                now,
                now,
                normalized["workers"],
                json.dumps(normalized),
            ),
        )
        conn.commit()
        study_id = int(cur.lastrowid)
    finally:
        conn.close()

    append_audit(
        db_path,
        category="study",
        action="create",
        entity_type="study",
        entity_id=study_id,
        details={"name": study_name, "spec": normalized},
    )
    return study_id


def set_study_status(
    db_path: str | Path,
    study_id: int,
    status: str,
    *,
    result: Mapping[str, Any] | None = None,
    error_text: str = "",
) -> None:
    if status not in VALID_STUDY_STATUSES:
        raise ValueError(f"Invalid study status: {status}")
    now = datetime.now().isoformat(timespec="seconds")

    conn = connect_db(db_path)
    try:
        conn.execute(
            """
            UPDATE study_jobs
            SET updated_at = ?,
                status = ?,
                result_json = ?,
                error_text = ?
            WHERE id = ?
            """,
            (
                now,
                status,
                json.dumps(dict(result)) if result is not None else None,
                str(error_text),
                int(study_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="study",
        action=f"status:{status.lower()}",
        entity_type="study",
        entity_id=study_id,
        details={
            "status": status,
            "has_result": result is not None,
            "error_text": str(error_text),
        },
    )


def mark_running_studies_interrupted(
    db_path: str | Path,
    *,
    exclude_id: int | None = None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        if exclude_id is None:
            cur = conn.execute(
                """
                UPDATE study_jobs
                SET status='INTERRUPTED', updated_at=?
                WHERE status='RUNNING'
                """,
                (now,),
            )
        else:
            cur = conn.execute(
                """
                UPDATE study_jobs
                SET status='INTERRUPTED', updated_at=?
                WHERE status='RUNNING' AND id != ?
                """,
                (now, int(exclude_id)),
            )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def list_studies(
    db_path: str | Path,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, name, created_at, updated_at, status, workers,
                   spec_json, result_json, error_text
            FROM study_jobs
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    output = []
    for row in rows:
        output.append(
            {
                "id": row[0],
                "name": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "status": row[4],
                "workers": row[5],
                "spec": json.loads(row[6]),
                "result": json.loads(row[7]) if row[7] else None,
                "error_text": row[8] or "",
            }
        )
    return output


def get_study(db_path: str | Path, study_id: int) -> dict[str, Any] | None:
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, name, created_at, updated_at, status, workers,
                   spec_json, result_json, error_text
            FROM study_jobs
            WHERE id = ?
            """,
            (int(study_id),),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "status": row[4],
        "workers": row[5],
        "spec": json.loads(row[6]),
        "result": json.loads(row[7]) if row[7] else None,
        "error_text": row[8] or "",
    }


def study_summary_text(study: Mapping[str, Any]) -> str:
    spec = study.get("spec") or {}
    result = study.get("result") or {}

    lines = [
        f"Study #{study.get('id')} · {study.get('name', '')}",
        f"Status:    {study.get('status', '')}",
        f"Created:   {study.get('created_at', '')}",
        f"Updated:   {study.get('updated_at', '')}",
        "",
        f"Events/job: {spec.get('events', '')}",
        f"Energies:   {spec.get('energies', [])}",
        f"Presets:    {spec.get('presets', [])}",
        f"Repeats:    {spec.get('repeats', '')}",
        f"Workers:    {spec.get('workers', '')}",
        f"Seed:       {spec.get('seed', '')}",
    ]

    if result:
        rows = result.get("results", [])
        lines += [
            "",
            f"Completed jobs: {result.get('jobs', len(rows))}",
            f"Execution workers: {result.get('workers', '')}",
        ]
        if rows:
            acceptance = [
                float(item.get("acceptance_rate", 0.0))
                for item in rows
            ]
            higgs = sum(int(item.get("higgs_count", 0)) for item in rows)
            lines += [
                f"Mean acceptance: {sum(acceptance) / len(acceptance):.3f}%",
                f"Total Higgs candidates: {higgs}",
            ]

    if study.get("error_text"):
        lines += ["", "ERROR", str(study["error_text"])]

    return "\n".join(lines)


__all__ = [
    "VALID_STUDY_STATUSES",
    "normalize_study_spec",
    "create_study",
    "set_study_status",
    "mark_running_studies_interrupted",
    "list_studies",
    "get_study",
    "study_summary_text",
]
