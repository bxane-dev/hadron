"""Campaign workspace support for Hadron v2.0."""

from __future__ import annotations

import json
import sqlite3
from hadron_db import connect_db
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from hadron_audit import append_audit
from hadron_capsule import list_capsules
from hadron_regression import list_regression_runs
from hadron_studies import create_study, get_study, list_studies, set_study_status
from hadron_study_analysis import normalized_results
from hadron_version import CAMPAIGN_FORMAT_VERSION, __version__


def create_campaign(
    db_path: str | Path,
    *,
    name: str,
    description: str = "",
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    campaign_name = str(name).strip() or "Untitled Campaign"

    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO campaigns(
                name, description, created_at, updated_at, archived
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (campaign_name, str(description), now, now),
        )
        campaign_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="campaign",
        action="create",
        entity_type="campaign",
        entity_id=campaign_id,
        details={"name": campaign_name},
    )
    return campaign_id


def list_campaigns(
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
            SELECT id, name, description, created_at, updated_at, archived
            FROM campaigns
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
            "description": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "archived": bool(row[5]),
        }
        for row in rows
    ]


def get_campaign(db_path: str | Path, campaign_id: int) -> dict[str, Any] | None:
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, name, description, created_at, updated_at, archived
            FROM campaigns
            WHERE id=?
            """,
            (int(campaign_id),),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None

    campaign = {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "created_at": row[3],
        "updated_at": row[4],
        "archived": bool(row[5]),
    }
    campaign["members"] = list_campaign_members(db_path, campaign_id)
    return campaign


def add_campaign_member(
    db_path: str | Path,
    campaign_id: int,
    *,
    member_type: str,
    member_id: int,
    label: str = "",
) -> int:
    allowed = {
        "study",
        "capsule",
        "regression",
        "reproduction",
        "template",
    }
    if member_type not in allowed:
        raise ValueError(f"Unsupported campaign member type: {member_type}")

    campaign = get_campaign(db_path, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign #{campaign_id} not found.")

    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO campaign_members(
                campaign_id, member_type, member_id, label, added_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(campaign_id),
                str(member_type),
                int(member_id),
                str(label),
                now,
            ),
        )
        if cur.lastrowid:
            membership_id = int(cur.lastrowid)
        else:
            row = conn.execute(
                """
                SELECT id FROM campaign_members
                WHERE campaign_id=? AND member_type=? AND member_id=?
                """,
                (int(campaign_id), str(member_type), int(member_id)),
            ).fetchone()
            membership_id = int(row[0])
        conn.execute(
            "UPDATE campaigns SET updated_at=? WHERE id=?",
            (now, int(campaign_id)),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="campaign",
        action="add-member",
        entity_type="campaign",
        entity_id=campaign_id,
        details={
            "member_type": member_type,
            "member_id": member_id,
            "membership_id": membership_id,
        },
    )
    return membership_id


def remove_campaign_member(
    db_path: str | Path,
    campaign_id: int,
    *,
    member_type: str,
    member_id: int,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        conn.execute(
            """
            DELETE FROM campaign_members
            WHERE campaign_id=? AND member_type=? AND member_id=?
            """,
            (int(campaign_id), str(member_type), int(member_id)),
        )
        conn.execute(
            "UPDATE campaigns SET updated_at=? WHERE id=?",
            (now, int(campaign_id)),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="campaign",
        action="remove-member",
        entity_type="campaign",
        entity_id=campaign_id,
        details={"member_type": member_type, "member_id": member_id},
    )


def list_campaign_members(
    db_path: str | Path,
    campaign_id: int,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, member_type, member_id, label, added_at
            FROM campaign_members
            WHERE campaign_id=?
            ORDER BY id ASC
            """,
            (int(campaign_id),),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "member_type": row[1],
            "member_id": row[2],
            "label": row[3],
            "added_at": row[4],
        }
        for row in rows
    ]


def archive_campaign(
    db_path: str | Path,
    campaign_id: int,
    *,
    archived: bool = True,
) -> None:
    conn = connect_db(db_path)
    try:
        conn.execute(
            """
            UPDATE campaigns
            SET archived=?, updated_at=?
            WHERE id=?
            """,
            (
                1 if archived else 0,
                datetime.now().isoformat(timespec="seconds"),
                int(campaign_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="campaign",
        action="archive" if archived else "restore",
        entity_type="campaign",
        entity_id=campaign_id,
        details={},
    )


def _lookup_capsule(db_path: str | Path, member_id: int) -> dict[str, Any] | None:
    return next(
        (item for item in list_capsules(db_path, limit=10000)
         if int(item["id"]) == int(member_id)),
        None,
    )


def _lookup_regression(db_path: str | Path, member_id: int) -> dict[str, Any] | None:
    return next(
        (item for item in list_regression_runs(db_path, limit=10000)
         if int(item["id"]) == int(member_id)),
        None,
    )


def _lookup_simple(
    db_path: str | Path,
    table: str,
    member_id: int,
) -> dict[str, Any] | None:
    if table not in {"reproduction_checks", "study_templates"}:
        raise ValueError("Unsupported campaign lookup table.")
    conn = connect_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id=?",
            (int(member_id),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _member_snapshot(
    db_path: str | Path,
    member: Mapping[str, Any],
) -> dict[str, Any]:
    member_type = member["member_type"]
    member_id = int(member["member_id"])

    if member_type == "study":
        payload = get_study(db_path, member_id)
        if payload:
            payload = dict(payload)
            rows = normalized_results(payload) if payload.get("result") else []
            payload["summary"] = {
                "jobs": len(rows),
                "events_total": sum(int(x.get("events_requested", 0)) for x in rows),
                "saved_total": sum(int(x.get("saved_count", 0)) for x in rows),
                "higgs_total": sum(int(x.get("higgs_count", 0)) for x in rows),
            }
    elif member_type == "capsule":
        payload = _lookup_capsule(db_path, member_id)
    elif member_type == "regression":
        payload = _lookup_regression(db_path, member_id)
    elif member_type == "reproduction":
        payload = _lookup_simple(db_path, "reproduction_checks", member_id)
    elif member_type == "template":
        payload = _lookup_simple(db_path, "study_templates", member_id)
    else:
        payload = None

    return {
        "member_type": member_type,
        "member_id": member_id,
        "label": member.get("label", ""),
        "added_at": member.get("added_at"),
        "snapshot": payload,
        "resolved": payload is not None,
    }


def campaign_snapshot(
    db_path: str | Path,
    campaign_id: int,
) -> dict[str, Any]:
    campaign = get_campaign(db_path, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign #{campaign_id} not found.")

    members = [
        _member_snapshot(db_path, item)
        for item in campaign["members"]
    ]

    studies = [
        item["snapshot"]
        for item in members
        if item["member_type"] == "study" and item["snapshot"]
    ]

    return {
        "format": "hadron-campaign",
        "format_version": CAMPAIGN_FORMAT_VERSION,
        "application": "Hadron",
        "application_version": __version__,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "campaign": {
            "id": campaign["id"],
            "name": campaign["name"],
            "description": campaign["description"],
            "created_at": campaign["created_at"],
            "updated_at": campaign["updated_at"],
            "archived": campaign["archived"],
        },
        "members": members,
        "summary": {
            "members": len(members),
            "resolved": sum(1 for item in members if item["resolved"]),
            "studies": len(studies),
            "study_jobs": sum(
                int((study.get("result") or {}).get("jobs", 0))
                for study in studies
            ),
            "study_events_total": sum(
                int((study.get("summary") or {}).get("events_total", 0))
                for study in studies
            ),
            "study_saved_total": sum(
                int((study.get("summary") or {}).get("saved_total", 0))
                for study in studies
            ),
        },
    }


def export_campaign_bundle(
    db_path: str | Path,
    campaign_id: int,
    destination: str | Path,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = campaign_snapshot(db_path, campaign_id)

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "campaign.json",
            json.dumps(payload, indent=2, sort_keys=True),
        )
        z.writestr(
            "README.txt",
            (
                "Hadron Campaign Bundle\n"
                f"Campaign: {payload['campaign']['name']}\n"
                f"Members: {payload['summary']['members']}\n"
                f"Hadron version: {payload['application_version']}\n"
                "This is a portable snapshot. It does not alter another database "
                "until explicitly imported.\n"
            ),
        )
    return destination


def import_campaign_bundle(
    db_path: str | Path,
    bundle_path: str | Path,
    *,
    restore_studies: bool = False,
) -> int:
    bundle_path = Path(bundle_path)
    with zipfile.ZipFile(bundle_path) as z:
        if "campaign.json" not in z.namelist():
            raise ValueError("Campaign bundle is missing campaign.json.")
        payload = json.loads(z.read("campaign.json").decode("utf-8"))

    if payload.get("format") != "hadron-campaign":
        raise ValueError("Not a Hadron campaign bundle.")
    if int(payload.get("format_version", 0)) != CAMPAIGN_FORMAT_VERSION:
        raise ValueError("Unsupported campaign format version.")

    source = payload.get("campaign") or {}
    campaign_id = create_campaign(
        db_path,
        name=f"{source.get('name', 'Imported Campaign')} (imported)",
        description=source.get("description", ""),
    )

    if restore_studies:
        for member in payload.get("members", []):
            if member.get("member_type") != "study":
                continue
            snapshot = member.get("snapshot")
            if not snapshot or not snapshot.get("result"):
                continue
            study_id = create_study(
                db_path,
                name=f"{snapshot.get('name', 'Study')} (campaign import)",
                spec=snapshot.get("spec") or {},
            )
            set_study_status(
                db_path,
                study_id,
                "COMPLETE",
                result=snapshot.get("result") or {},
            )
            add_campaign_member(
                db_path,
                campaign_id,
                member_type="study",
                member_id=study_id,
                label=member.get("label", ""),
            )

    append_audit(
        db_path,
        category="campaign",
        action="import",
        entity_type="campaign",
        entity_id=campaign_id,
        details={
            "source_campaign_id": source.get("id"),
            "bundle_name": bundle_path.name,
            "restore_studies": bool(restore_studies),
        },
    )
    return campaign_id


def campaign_summary_text(payload: Mapping[str, Any]) -> str:
    campaign = payload.get("campaign") or {}
    summary = payload.get("summary") or {}

    lines = [
        f"Campaign #{campaign.get('id')} · {campaign.get('name', '')}",
        f"Updated: {campaign.get('updated_at', '')}",
        "",
        str(campaign.get("description", "")),
        "",
        f"Members:             {summary.get('members', 0)}",
        f"Resolved locally:    {summary.get('resolved', 0)}",
        f"Studies:             {summary.get('studies', 0)}",
        f"Study jobs:          {summary.get('study_jobs', 0)}",
        f"Generated events:    {summary.get('study_events_total', 0):,}",
        f"Saved events:        {summary.get('study_saved_total', 0):,}",
        "",
        "MEMBERS",
    ]

    for member in payload.get("members", []):
        marker = "OK" if member.get("resolved") else "MISSING"
        lines.append(
            f"[{marker:<7}] {member.get('member_type', ''):<12} "
            f"#{member.get('member_id')}  {member.get('label', '')}"
        )

    return "\n".join(lines)


__all__ = [
    "create_campaign",
    "list_campaigns",
    "get_campaign",
    "add_campaign_member",
    "remove_campaign_member",
    "list_campaign_members",
    "archive_campaign",
    "campaign_snapshot",
    "export_campaign_bundle",
    "import_campaign_bundle",
    "campaign_summary_text",
]
