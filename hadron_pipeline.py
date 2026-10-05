"""Campaign pipeline automation for Hadron v2.2."""

from __future__ import annotations

import json
import sqlite3
from hadron_db import connect_db
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_audit import append_audit
from hadron_campaign_run import (
    campaign_health,
    check_campaign_reference,
    create_campaign_report_bundle,
    list_campaign_references,
    run_campaign_to_database,
    save_campaign_reference,
)
from hadron_version import PIPELINE_RUN_VERSION, __version__


DEFAULT_STAGES = [
    "health",
    "reference-check",
    "campaign-run",
    "report",
]


def normalize_pipeline_spec(
    spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(spec or {})
    stages = raw.get("stages", DEFAULT_STAGES)
    if not isinstance(stages, list) or not stages:
        raise ValueError("Pipeline stages must be a non-empty list.")

    allowed = {
        "health",
        "ensure-reference",
        "reference-check",
        "campaign-run",
        "report",
    }
    normalized_stages = []
    for stage in stages:
        stage = str(stage).strip().lower()
        if stage not in allowed:
            raise ValueError(f"Unsupported pipeline stage: {stage}")
        if stage not in normalized_stages:
            normalized_stages.append(stage)

    return {
        "stages": normalized_stages,
        "workers": max(1, min(8, int(raw.get("workers", 1)))),
        "require_healthy": bool(raw.get("require_healthy", True)),
        "require_reference_match": bool(
            raw.get("require_reference_match", True)
        ),
        "require_campaign_pass": bool(
            raw.get("require_campaign_pass", True)
        ),
        "auto_create_reference": bool(
            raw.get("auto_create_reference", False)
        ),
    }


def create_pipeline(
    db_path: str | Path,
    *,
    name: str,
    campaign_id: int,
    spec: Mapping[str, Any] | None = None,
) -> int:
    normalized = normalize_pipeline_spec(spec)
    now = datetime.now().isoformat(timespec="seconds")

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipelines(
                name, campaign_id, created_at, updated_at, spec_json, archived
            )
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                str(name).strip() or "Campaign Pipeline",
                int(campaign_id),
                now,
                now,
                json.dumps(normalized),
            ),
        )
        pipeline_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="pipeline",
        action="create",
        entity_type="pipeline",
        entity_id=pipeline_id,
        details={
            "campaign_id": int(campaign_id),
            "name": str(name),
        },
    )
    return pipeline_id


def list_pipelines(
    db_path: str | Path,
    *,
    include_archived: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        where = "" if include_archived else "WHERE archived=0"
        rows = conn.execute(
            f"""
            SELECT id, name, campaign_id, created_at, updated_at,
                   spec_json, archived
            FROM pipelines
            {where}
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
            "campaign_id": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "spec": json.loads(row[5]),
            "archived": bool(row[6]),
        }
        for row in rows
    ]


def get_pipeline(
    db_path: str | Path,
    pipeline_id: int,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in list_pipelines(
                db_path,
                include_archived=True,
                limit=10000,
            )
            if int(item["id"]) == int(pipeline_id)
        ),
        None,
    )


def _stage_result(
    name: str,
    ok: bool,
    *,
    detail: Mapping[str, Any] | None = None,
    skipped: bool = False,
) -> dict[str, Any]:
    return {
        "stage": name,
        "ok": bool(ok),
        "skipped": bool(skipped),
        "detail": dict(detail or {}),
    }


def run_pipeline(
    db_path: str | Path,
    pipeline_id: int,
    *,
    report_dir: str | Path | None = None,
) -> dict[str, Any]:
    pipeline = get_pipeline(db_path, pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline #{pipeline_id} not found.")

    spec = normalize_pipeline_spec(pipeline["spec"])
    campaign_id = int(pipeline["campaign_id"])
    stages = []
    halted = False
    report_path = None

    for stage in spec["stages"]:
        if halted:
            stages.append(
                _stage_result(
                    stage,
                    False,
                    skipped=True,
                    detail={"reason": "previous gate failed"},
                )
            )
            continue

        if stage == "health":
            health = campaign_health(db_path, campaign_id)
            ok = health["healthy"] or not spec["require_healthy"]
            stages.append(_stage_result(stage, ok, detail=health))
            if not ok:
                halted = True

        elif stage == "ensure-reference":
            refs = list_campaign_references(
                db_path,
                campaign_id,
                limit=1,
            )
            created = False
            if not refs and spec["auto_create_reference"]:
                reference_id = save_campaign_reference(
                    db_path,
                    campaign_id,
                    name="Pipeline Reference",
                )
                refs = list_campaign_references(
                    db_path,
                    campaign_id,
                    limit=1,
                )
                created = True
            else:
                reference_id = refs[0]["id"] if refs else None

            ok = bool(refs) or not spec["require_reference_match"]
            stages.append(
                _stage_result(
                    stage,
                    ok,
                    detail={
                        "reference_id": reference_id,
                        "created": created,
                    },
                )
            )
            if not ok:
                halted = True

        elif stage == "reference-check":
            refs = list_campaign_references(
                db_path,
                campaign_id,
                limit=1,
            )
            if not refs:
                detail = {"reason": "no saved reference"}
                ok = not spec["require_reference_match"]
            else:
                detail = check_campaign_reference(
                    db_path,
                    refs[0]["payload"],
                )
                ok = (
                    detail["matches"]
                    or not spec["require_reference_match"]
                )

            stages.append(_stage_result(stage, ok, detail=detail))
            if not ok:
                halted = True

        elif stage == "campaign-run":
            result = run_campaign_to_database(
                db_path,
                campaign_id,
                workers_override=spec["workers"],
            )
            ok = (
                result["all_passed"]
                or not spec["require_campaign_pass"]
            )
            stages.append(_stage_result(stage, ok, detail=result))
            if not ok:
                halted = True

        elif stage == "report":
            if report_dir is None:
                stages.append(
                    _stage_result(
                        stage,
                        True,
                        detail={
                            "generated": False,
                            "reason": "no report directory requested",
                        },
                    )
                )
            else:
                report_dir = Path(report_dir)
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = (
                    report_dir
                    / (
                        f"pipeline-{pipeline_id}-campaign-{campaign_id}"
                        ".hadron-campaign-report.zip"
                    )
                )
                create_campaign_report_bundle(
                    db_path,
                    campaign_id,
                    report_path,
                )
                stages.append(
                    _stage_result(
                        stage,
                        True,
                        detail={
                            "generated": True,
                            "path": str(report_path),
                        },
                    )
                )

    passed = not any(
        (not item["ok"] and not item["skipped"])
        for item in stages
    )

    return {
        "format": "hadron-pipeline-run",
        "format_version": PIPELINE_RUN_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pipeline_id": pipeline_id,
        "pipeline_name": pipeline["name"],
        "campaign_id": campaign_id,
        "spec": spec,
        "passed": passed,
        "halted": halted,
        "report_path": str(report_path) if report_path else None,
        "stages": stages,
    }


def record_pipeline_run(
    db_path: str | Path,
    report: Mapping[str, Any],
) -> int:
    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs(
                pipeline_id, campaign_id, created_at,
                passed, halted, report_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(report["pipeline_id"]),
                int(report["campaign_id"]),
                datetime.now().isoformat(timespec="seconds"),
                1 if report.get("passed") else 0,
                1 if report.get("halted") else 0,
                json.dumps(dict(report)),
            ),
        )
        run_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="pipeline",
        action="run",
        entity_type="pipeline_run",
        entity_id=run_id,
        details={
            "pipeline_id": report["pipeline_id"],
            "campaign_id": report["campaign_id"],
            "passed": bool(report.get("passed")),
        },
    )
    return run_id


def run_pipeline_to_database(
    db_path: str | Path,
    pipeline_id: int,
    *,
    report_dir: str | Path | None = None,
) -> dict[str, Any]:
    report = run_pipeline(
        db_path,
        pipeline_id,
        report_dir=report_dir,
    )
    report["database_run_id"] = record_pipeline_run(
        db_path,
        report,
    )
    return report


def list_pipeline_runs(
    db_path: str | Path,
    pipeline_id: int | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        if pipeline_id is None:
            rows = conn.execute(
                """
                SELECT id, pipeline_id, campaign_id, created_at,
                       passed, halted, report_json
                FROM pipeline_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, pipeline_id, campaign_id, created_at,
                       passed, halted, report_json
                FROM pipeline_runs
                WHERE pipeline_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(pipeline_id), int(limit)),
            ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "pipeline_id": row[1],
            "campaign_id": row[2],
            "created_at": row[3],
            "passed": bool(row[4]),
            "halted": bool(row[5]),
            "report": json.loads(row[6]),
        }
        for row in rows
    ]


def pipeline_to_junit(
    report: Mapping[str, Any],
) -> str:
    stages = list(report.get("stages", []))
    failures = sum(
        1
        for stage in stages
        if not stage.get("ok") and not stage.get("skipped")
    )

    suite = ET.Element(
        "testsuite",
        {
            "name": "HadronCampaignPipeline",
            "tests": str(len(stages)),
            "failures": str(failures),
            "errors": "0",
        },
    )

    for stage in stages:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "hadron.pipeline",
                "name": str(stage.get("stage")),
            },
        )
        if stage.get("skipped"):
            ET.SubElement(case, "skipped")
        elif not stage.get("ok"):
            failure = ET.SubElement(
                case,
                "failure",
                {
                    "message": "Pipeline stage failed",
                    "type": "HadronPipelineFailure",
                },
            )
            failure.text = json.dumps(
                stage.get("detail") or {},
                indent=2,
            )

    return ET.tostring(suite, encoding="unicode")


__all__ = [
    "normalize_pipeline_spec",
    "create_pipeline",
    "list_pipelines",
    "get_pipeline",
    "run_pipeline",
    "record_pipeline_run",
    "run_pipeline_to_database",
    "list_pipeline_runs",
    "pipeline_to_junit",
]
