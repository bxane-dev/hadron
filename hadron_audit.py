"""Hash-chained audit log for Hadron v1.6."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from hadron_db import connect_db
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def _entry_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def append_audit(
    db_path: str | Path,
    *,
    category: str,
    action: str,
    entity_type: str = "",
    entity_id: int | str | None = None,
    details: Mapping[str, Any] | None = None,
) -> int:
    timestamp = datetime.now().isoformat(timespec="microseconds")
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(row[0]) if row else ("0" * 64)

        payload = {
            "timestamp": timestamp,
            "category": str(category),
            "action": str(action),
            "entity_type": str(entity_type),
            "entity_id": None if entity_id is None else str(entity_id),
            "details": dict(details or {}),
            "previous_hash": previous_hash,
        }
        entry_hash = _entry_hash(payload)

        cur = conn.execute(
            """
            INSERT INTO audit_log(
                timestamp, category, action, entity_type, entity_id,
                details_json, previous_hash, entry_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["timestamp"],
                payload["category"],
                payload["action"],
                payload["entity_type"],
                payload["entity_id"],
                json.dumps(payload["details"], sort_keys=True),
                previous_hash,
                entry_hash,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_audit(
    db_path: str | Path,
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, category, action, entity_type, entity_id,
                   details_json, previous_hash, entry_hash
            FROM audit_log
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
                "timestamp": row[1],
                "category": row[2],
                "action": row[3],
                "entity_type": row[4],
                "entity_id": row[5],
                "details": json.loads(row[6]) if row[6] else {},
                "previous_hash": row[7],
                "entry_hash": row[8],
            }
        )
    return output


def verify_audit_chain(db_path: str | Path) -> dict[str, Any]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, category, action, entity_type, entity_id,
                   details_json, previous_hash, entry_hash
            FROM audit_log
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    expected_previous = "0" * 64
    checks = []

    for row in rows:
        payload = {
            "timestamp": row[1],
            "category": row[2],
            "action": row[3],
            "entity_type": row[4],
            "entity_id": row[5],
            "details": json.loads(row[6]) if row[6] else {},
            "previous_hash": row[7],
        }
        computed = _entry_hash(payload)
        previous_ok = row[7] == expected_previous
        hash_ok = row[8] == computed
        ok = previous_ok and hash_ok
        checks.append(
            {
                "id": row[0],
                "previous_ok": previous_ok,
                "hash_ok": hash_ok,
                "ok": ok,
                "stored_hash": row[8],
                "computed_hash": computed,
            }
        )
        expected_previous = row[8]

    return {
        "entries": len(rows),
        "head_hash": expected_previous if rows else ("0" * 64),
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


__all__ = ["append_audit", "list_audit", "verify_audit_chain"]
