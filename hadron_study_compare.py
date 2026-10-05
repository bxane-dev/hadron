"""Study comparison and template helpers for Hadron v1.4."""

from __future__ import annotations

import json
import sqlite3
from hadron_db import connect_db
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from hadron_audit import append_audit
from hadron_studies import normalize_study_spec
from hadron_study_analysis import aggregate_by_preset_energy, normalized_results


def compare_studies(
    study_a: Mapping[str, Any],
    study_b: Mapping[str, Any],
) -> dict[str, Any]:
    rows_a = normalized_results(study_a)
    rows_b = normalized_results(study_b)

    agg_a = {
        (row["preset"], float(row["beam_energy_gev"])): row
        for row in aggregate_by_preset_energy(rows_a)
    }
    agg_b = {
        (row["preset"], float(row["beam_energy_gev"])): row
        for row in aggregate_by_preset_energy(rows_b)
    }

    keys = sorted(set(agg_a) | set(agg_b))
    grid = []
    for key in keys:
        a = agg_a.get(key)
        b = agg_b.get(key)
        acceptance_a = float(a["acceptance_mean"]) if a else None
        acceptance_b = float(b["acceptance_mean"]) if b else None
        higgs_a = float(a["higgs_rate_mean_per_1000"]) if a else None
        higgs_b = float(b["higgs_rate_mean_per_1000"]) if b else None

        grid.append(
            {
                "preset": key[0],
                "beam_energy_gev": key[1],
                "acceptance_a": acceptance_a,
                "acceptance_b": acceptance_b,
                "acceptance_delta_pp": (
                    acceptance_a - acceptance_b
                    if acceptance_a is not None and acceptance_b is not None
                    else None
                ),
                "higgs_rate_a": higgs_a,
                "higgs_rate_b": higgs_b,
                "higgs_rate_delta": (
                    higgs_a - higgs_b
                    if higgs_a is not None and higgs_b is not None
                    else None
                ),
            }
        )

    return {
        "study_a": {
            "id": study_a.get("id"),
            "name": study_a.get("name"),
            "spec": study_a.get("spec"),
        },
        "study_b": {
            "id": study_b.get("id"),
            "name": study_b.get("name"),
            "spec": study_b.get("spec"),
        },
        "config_diff": diff_specs(
            study_a.get("spec") or {},
            study_b.get("spec") or {},
        ),
        "grid": grid,
    }


def diff_specs(
    spec_a: Mapping[str, Any],
    spec_b: Mapping[str, Any],
) -> list[dict[str, Any]]:
    keys = sorted(set(spec_a) | set(spec_b))
    output = []
    for key in keys:
        a = spec_a.get(key)
        b = spec_b.get(key)
        if a != b:
            output.append({"field": key, "a": a, "b": b})
    return output


def clone_study_spec(
    study: Mapping[str, Any],
    *,
    workers: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    spec = dict(study.get("spec") or {})
    if workers is not None:
        spec["workers"] = int(workers)
    if seed is not None:
        spec["seed"] = int(seed)
    return normalize_study_spec(spec)


def save_template(
    db_path: str | Path,
    *,
    name: str,
    spec: Mapping[str, Any],
    source_study_id: int | None = None,
) -> int:
    normalized = normalize_study_spec(spec)
    now = datetime.now().isoformat(timespec="seconds")
    template_name = str(name).strip() or "Untitled Template"

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO study_templates(
                name, created_at, updated_at, source_study_id, spec_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                template_name,
                now,
                now,
                source_study_id,
                json.dumps(normalized),
            ),
        )
        conn.commit()
        template_id = int(cur.lastrowid)
    finally:
        conn.close()

    append_audit(
        db_path,
        category="template",
        action="create",
        entity_type="study_template",
        entity_id=template_id,
        details={
            "name": template_name,
            "source_study_id": source_study_id,
        },
    )
    return template_id


def list_templates(
    db_path: str | Path,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, name, created_at, updated_at, source_study_id, spec_json
            FROM study_templates
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
            "name": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "source_study_id": row[4],
            "spec": json.loads(row[5]),
        }
        for row in rows
    ]


def delete_template(db_path: str | Path, template_id: int) -> None:
    conn = connect_db(db_path)
    try:
        conn.execute(
            "DELETE FROM study_templates WHERE id = ?",
            (int(template_id),),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="template",
        action="delete",
        entity_type="study_template",
        entity_id=template_id,
        details={},
    )


def provenance_payload(
    study: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "study_id": study.get("id"),
        "name": study.get("name"),
        "status": study.get("status"),
        "created_at": study.get("created_at"),
        "updated_at": study.get("updated_at"),
        "spec": study.get("spec"),
        "result_version": (study.get("result") or {}).get("version"),
        "jobs": (study.get("result") or {}).get("jobs"),
        "workers": (study.get("result") or {}).get("workers"),
    }


__all__ = [
    "compare_studies",
    "diff_specs",
    "clone_study_spec",
    "save_template",
    "list_templates",
    "delete_template",
    "provenance_payload",
]
