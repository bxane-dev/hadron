"""Executable campaign workflows for Hadron v2.1."""

from __future__ import annotations

import csv
import html
import io
import json
import sqlite3
from hadron_db import connect_db
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_audit import append_audit
from hadron_campaign import campaign_snapshot, get_campaign
from hadron_jobs import run_job_spec
from hadron_release import canonical_json_hash
from hadron_reproduce import stable_job_hash
from hadron_studies import get_study
from hadron_version import (
    CAMPAIGN_REFERENCE_VERSION,
    CAMPAIGN_RUN_VERSION,
    __version__,
)


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _study_fingerprint(study: Mapping[str, Any]) -> dict[str, Any]:
    result = study.get("result") or {}
    rows = list(result.get("results") or [])
    return {
        "spec_sha256": canonical_json_hash(study.get("spec") or {}),
        "job_hashes": [stable_job_hash(row) for row in rows],
        "jobs": len(rows),
    }


def _member_fingerprint(member: Mapping[str, Any]) -> dict[str, Any]:
    member_type = str(member.get("member_type", ""))
    snapshot = member.get("snapshot")
    if snapshot is None:
        return {
            "member_type": member_type,
            "member_id": member.get("member_id"),
            "resolved": False,
            "content_sha256": None,
        }

    if member_type == "study":
        fingerprint = _study_fingerprint(snapshot)
    elif member_type == "capsule":
        fingerprint = {
            "capsule_sha256": snapshot.get("capsule_sha256"),
            "verified": bool(snapshot.get("verified")),
        }
    elif member_type == "regression":
        fingerprint = {
            "all_passed": bool(snapshot.get("all_passed")),
            "report_sha256": canonical_json_hash(
                _decode_json(snapshot.get("report")) or {}
            ),
        }
    elif member_type == "reproduction":
        report = _decode_json(snapshot.get("report_json")) or {}
        fingerprint = {
            "exact_reproduction": bool(snapshot.get("exact_reproduction")),
            "report_sha256": canonical_json_hash(report),
        }
    elif member_type == "template":
        spec = _decode_json(snapshot.get("spec_json")) or {}
        fingerprint = {
            "spec_sha256": canonical_json_hash(spec),
        }
    else:
        fingerprint = {
            "snapshot_sha256": canonical_json_hash(snapshot),
        }

    return {
        "member_type": member_type,
        "member_id": member.get("member_id"),
        "resolved": True,
        **fingerprint,
    }


def campaign_health(
    db_path: str | Path,
    campaign_id: int,
) -> dict[str, Any]:
    payload = campaign_snapshot(db_path, campaign_id)
    rows = []

    for member in payload.get("members", []):
        member_type = member["member_type"]
        snapshot = member.get("snapshot")
        status = "OK"
        reason = ""

        if not member.get("resolved"):
            status = "MISSING"
            reason = "Member cannot be resolved in the local database."
        elif member_type == "study":
            if not snapshot.get("result"):
                status = "INCOMPLETE"
                reason = "Study has no completed result payload."
        elif member_type == "capsule":
            if not snapshot.get("verified"):
                status = "FAILED"
                reason = "Capsule registry entry is not verified."
        elif member_type == "regression":
            if not snapshot.get("all_passed"):
                status = "FAILED"
                reason = "Regression run did not pass."
        elif member_type == "reproduction":
            if not snapshot.get("exact_reproduction"):
                status = "FAILED"
                reason = "Reproduction check was not an exact match."

        rows.append(
            {
                "member_type": member_type,
                "member_id": member["member_id"],
                "label": member.get("label", ""),
                "status": status,
                "reason": reason,
                "healthy": status == "OK",
            }
        )

    return {
        "format": "hadron-campaign-health",
        "application": "Hadron",
        "application_version": __version__,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "campaign_id": campaign_id,
        "campaign_name": payload["campaign"]["name"],
        "members": len(rows),
        "healthy_members": sum(1 for row in rows if row["healthy"]),
        "unhealthy_members": sum(1 for row in rows if not row["healthy"]),
        "healthy": bool(rows) and all(row["healthy"] for row in rows),
        "rows": rows,
    }


def create_campaign_reference_payload(
    db_path: str | Path,
    campaign_id: int,
) -> dict[str, Any]:
    payload = campaign_snapshot(db_path, campaign_id)
    fingerprints = [
        _member_fingerprint(member)
        for member in payload.get("members", [])
    ]

    return {
        "format": "hadron-campaign-reference",
        "format_version": CAMPAIGN_REFERENCE_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "campaign_id": campaign_id,
        "campaign_name": payload["campaign"]["name"],
        "members": fingerprints,
        "member_count": len(fingerprints),
        "reference_sha256": canonical_json_hash(fingerprints),
    }


def save_campaign_reference(
    db_path: str | Path,
    campaign_id: int,
    *,
    name: str = "Reference",
) -> int:
    reference = create_campaign_reference_payload(db_path, campaign_id)
    now = datetime.now().isoformat(timespec="seconds")

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO campaign_references(
                campaign_id, name, created_at, reference_sha256,
                member_count, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(campaign_id),
                str(name).strip() or "Reference",
                now,
                reference["reference_sha256"],
                int(reference["member_count"]),
                json.dumps(reference),
            ),
        )
        reference_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="campaign",
        action="save-reference",
        entity_type="campaign_reference",
        entity_id=reference_id,
        details={
            "campaign_id": campaign_id,
            "reference_sha256": reference["reference_sha256"],
        },
    )
    return reference_id


def list_campaign_references(
    db_path: str | Path,
    campaign_id: int | None = None,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        if campaign_id is None:
            rows = conn.execute(
                """
                SELECT id, campaign_id, name, created_at, reference_sha256,
                       member_count, payload_json
                FROM campaign_references
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, campaign_id, name, created_at, reference_sha256,
                       member_count, payload_json
                FROM campaign_references
                WHERE campaign_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(campaign_id), int(limit)),
            ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "campaign_id": row[1],
            "name": row[2],
            "created_at": row[3],
            "reference_sha256": row[4],
            "member_count": row[5],
            "payload": json.loads(row[6]),
        }
        for row in rows
    ]


def check_campaign_reference(
    db_path: str | Path,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    if reference.get("format") != "hadron-campaign-reference":
        raise ValueError("Not a Hadron campaign reference.")

    campaign_id = int(reference["campaign_id"])
    current = create_campaign_reference_payload(db_path, campaign_id)

    expected_by_key = {
        (item["member_type"], int(item["member_id"])): item
        for item in reference.get("members", [])
    }
    current_by_key = {
        (item["member_type"], int(item["member_id"])): item
        for item in current.get("members", [])
    }

    keys = sorted(set(expected_by_key) | set(current_by_key))
    rows = []
    for key in keys:
        expected = expected_by_key.get(key)
        actual = current_by_key.get(key)
        status = "UNCHANGED"
        if expected is None:
            status = "ADDED"
        elif actual is None:
            status = "REMOVED"
        elif expected != actual:
            status = "CHANGED"

        rows.append(
            {
                "member_type": key[0],
                "member_id": key[1],
                "status": status,
                "expected": expected,
                "current": actual,
                "matches": status == "UNCHANGED",
            }
        )

    return {
        "format": "hadron-campaign-reference-check",
        "application": "Hadron",
        "application_version": __version__,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "campaign_id": campaign_id,
        "reference_sha256": reference.get("reference_sha256"),
        "current_sha256": current["reference_sha256"],
        "members_checked": len(rows),
        "members_matching": sum(1 for row in rows if row["matches"]),
        "drifted_members": sum(1 for row in rows if not row["matches"]),
        "matches": bool(rows) and all(row["matches"] for row in rows),
        "rows": rows,
    }


def run_campaign(
    db_path: str | Path,
    campaign_id: int,
    *,
    workers_override: int = 1,
) -> dict[str, Any]:
    if not 1 <= int(workers_override) <= 8:
        raise ValueError("workers_override must be between 1 and 8.")

    campaign = get_campaign(db_path, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign #{campaign_id} not found.")

    study_members = [
        member
        for member in campaign["members"]
        if member["member_type"] == "study"
    ]

    rows = []
    for member in study_members:
        study_id = int(member["member_id"])
        row = {
            "study_id": study_id,
            "label": member.get("label", ""),
            "status": "MISSING",
            "matching": False,
        }

        try:
            study = get_study(db_path, study_id)
            if study is None:
                row["error"] = "Study not found."
            elif not study.get("result"):
                row["status"] = "INCOMPLETE"
                row["error"] = "Study has no saved result."
            else:
                original_rows = list(
                    (study.get("result") or {}).get("results") or []
                )
                rerun = run_job_spec(
                    study.get("spec") or {},
                    workers=int(workers_override),
                )
                reproduced_rows = list(rerun.get("results") or [])

                original_hashes = [
                    stable_job_hash(item) for item in original_rows
                ]
                reproduced_hashes = [
                    stable_job_hash(item) for item in reproduced_rows
                ]

                row.update(
                    {
                        "study_name": study.get("name"),
                        "status": (
                            "PASS"
                            if original_hashes == reproduced_hashes
                            else "DIVERGENCE"
                        ),
                        "matching": original_hashes == reproduced_hashes,
                        "jobs_original": len(original_hashes),
                        "jobs_reproduced": len(reproduced_hashes),
                        "original_hashes": original_hashes,
                        "reproduced_hashes": reproduced_hashes,
                        "rerun_result": rerun,
                    }
                )
        except Exception as exc:
            row["status"] = "ERROR"
            row["error"] = f"{exc.__class__.__name__}: {exc}"

        rows.append(row)

    report = {
        "format": "hadron-campaign-run",
        "format_version": CAMPAIGN_RUN_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "campaign_id": campaign_id,
        "campaign_name": campaign["name"],
        "workers_used": int(workers_override),
        "studies": len(rows),
        "passed": sum(1 for row in rows if row["status"] == "PASS"),
        "diverged": sum(1 for row in rows if row["status"] == "DIVERGENCE"),
        "incomplete": sum(1 for row in rows if row["status"] == "INCOMPLETE"),
        "missing": sum(1 for row in rows if row["status"] == "MISSING"),
        "errors": sum(1 for row in rows if row["status"] == "ERROR"),
        "all_passed": bool(rows) and all(row["matching"] for row in rows),
        "rows": rows,
    }
    return report


def record_campaign_run(
    db_path: str | Path,
    report: Mapping[str, Any],
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    failed = (
        int(report.get("diverged", 0))
        + int(report.get("incomplete", 0))
        + int(report.get("missing", 0))
        + int(report.get("errors", 0))
    )

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO campaign_runs(
                campaign_id, created_at, studies, passed, failed,
                all_passed, report_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(report["campaign_id"]),
                now,
                int(report.get("studies", 0)),
                int(report.get("passed", 0)),
                failed,
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
        category="campaign",
        action="run",
        entity_type="campaign_run",
        entity_id=run_id,
        details={
            "campaign_id": report["campaign_id"],
            "all_passed": bool(report.get("all_passed")),
            "passed": int(report.get("passed", 0)),
            "failed": failed,
        },
    )
    return run_id


def run_campaign_to_database(
    db_path: str | Path,
    campaign_id: int,
    *,
    workers_override: int = 1,
) -> dict[str, Any]:
    report = run_campaign(
        db_path,
        campaign_id,
        workers_override=workers_override,
    )
    report["database_run_id"] = record_campaign_run(db_path, report)
    return report


def list_campaign_runs(
    db_path: str | Path,
    campaign_id: int | None = None,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        if campaign_id is None:
            rows = conn.execute(
                """
                SELECT id, campaign_id, created_at, studies, passed, failed,
                       all_passed, report_json
                FROM campaign_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, campaign_id, created_at, studies, passed, failed,
                       all_passed, report_json
                FROM campaign_runs
                WHERE campaign_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(campaign_id), int(limit)),
            ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "campaign_id": row[1],
            "created_at": row[2],
            "studies": row[3],
            "passed": row[4],
            "failed": row[5],
            "all_passed": bool(row[6]),
            "report": json.loads(row[7]),
        }
        for row in rows
    ]


def _health_csv(health: Mapping[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "member_type",
            "member_id",
            "label",
            "status",
            "healthy",
            "reason",
        ],
    )
    writer.writeheader()
    for row in health.get("rows", []):
        writer.writerow(row)
    return buffer.getvalue()


def _campaign_html(
    snapshot: Mapping[str, Any],
    health: Mapping[str, Any],
    latest_run: Mapping[str, Any] | None,
) -> str:
    campaign = snapshot["campaign"]
    run_rows = ""
    if latest_run:
        run_rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(str(row.get('study_name') or row.get('study_id')))}</td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{'yes' if row.get('matching') else 'no'}</td>"
            "</tr>"
            for row in latest_run.get("report", {}).get("rows", [])
        )

    health_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(row['member_type']))}</td>"
        f"<td>{row['member_id']}</td>"
        f"<td>{html.escape(str(row['label']))}</td>"
        f"<td>{html.escape(str(row['status']))}</td>"
        "</tr>"
        for row in health.get("rows", [])
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Hadron Campaign Report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 32px; background: #0d1117; color: #e6edf3; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0 28px; }}
th, td {{ border: 1px solid #30363d; padding: 8px 10px; text-align: left; }}
th {{ background: #161b22; }}
code {{ color: #79c0ff; }}
</style>
</head>
<body>
<h1>{html.escape(str(campaign['name']))}</h1>
<p>{html.escape(str(campaign.get('description', '')))}</p>
<p>Hadron v{__version__} · campaign #{campaign['id']}</p>
<h2>Health</h2>
<p>{health['healthy_members']} / {health['members']} members healthy.</p>
<table>
<tr><th>Type</th><th>ID</th><th>Label</th><th>Status</th></tr>
{health_rows}
</table>
<h2>Latest campaign run</h2>
<table>
<tr><th>Study</th><th>Status</th><th>Deterministic match</th></tr>
{run_rows or '<tr><td colspan="3">No recorded campaign run.</td></tr>'}
</table>
</body>
</html>"""


def create_campaign_report_bundle(
    db_path: str | Path,
    campaign_id: int,
    destination: str | Path,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    snapshot = campaign_snapshot(db_path, campaign_id)
    health = campaign_health(db_path, campaign_id)
    runs = list_campaign_runs(db_path, campaign_id, limit=1)
    references = list_campaign_references(db_path, campaign_id, limit=1)
    latest_run = runs[0] if runs else None
    latest_reference = references[0] if references else None

    reference_check = None
    if latest_reference:
        reference_check = check_campaign_reference(
            db_path,
            latest_reference["payload"],
        )

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("campaign.json", json.dumps(snapshot, indent=2))
        z.writestr("health.json", json.dumps(health, indent=2))
        z.writestr("health.csv", _health_csv(health))
        z.writestr(
            "latest-campaign-run.json",
            json.dumps(latest_run, indent=2),
        )
        z.writestr(
            "latest-reference.json",
            json.dumps(latest_reference, indent=2),
        )
        z.writestr(
            "reference-check.json",
            json.dumps(reference_check, indent=2),
        )
        z.writestr(
            "report.html",
            _campaign_html(snapshot, health, latest_run),
        )
        z.writestr(
            "README.txt",
            (
                "Hadron Campaign Report Bundle\n"
                f"Campaign: {snapshot['campaign']['name']}\n"
                f"Hadron: {__version__}\n"
                "Contents: campaign snapshot, member health, latest deterministic "
                "campaign run, latest reference check, CSV and HTML report.\n"
            ),
        )
    return destination


__all__ = [
    "campaign_health",
    "create_campaign_reference_payload",
    "save_campaign_reference",
    "list_campaign_references",
    "check_campaign_reference",
    "run_campaign",
    "record_campaign_run",
    "run_campaign_to_database",
    "list_campaign_runs",
    "create_campaign_report_bundle",
]
