"""Trusted Ed25519 public-key store for Hadron v1.7."""

from __future__ import annotations

import sqlite3
from hadron_db import connect_db
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hadron_audit import append_audit
from hadron_release import public_key_fingerprint, verify_release_signature


def add_trusted_key(
    db_path: str | Path,
    *,
    label: str,
    public_key_path: str | Path,
) -> int:
    public_key_path = Path(public_key_path)
    pem = public_key_path.read_text(encoding="utf-8")
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Trusted key must be an Ed25519 public key.")

    fingerprint = public_key_fingerprint(public_key_path)
    now = datetime.now().isoformat(timespec="seconds")
    key_label = str(label).strip() or public_key_path.stem

    conn = connect_db(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM trusted_public_keys WHERE fingerprint_sha256 = ?",
            (fingerprint,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE trusted_public_keys
                SET label=?, pem_text=?, active=1, updated_at=?
                WHERE id=?
                """,
                (key_label, pem, now, int(row[0])),
            )
            key_id = int(row[0])
        else:
            cur = conn.execute(
                """
                INSERT INTO trusted_public_keys(
                    label, created_at, updated_at, fingerprint_sha256,
                    pem_text, active
                )
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (key_label, now, now, fingerprint, pem),
            )
            key_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="trust",
        action="add-key",
        entity_type="trusted_public_key",
        entity_id=key_id,
        details={"label": key_label, "fingerprint_sha256": fingerprint},
    )
    return key_id


def list_trusted_keys(
    db_path: str | Path,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        where = "WHERE active = 1" if active_only else ""
        rows = conn.execute(
            f"""
            SELECT id, label, created_at, updated_at, fingerprint_sha256,
                   pem_text, active
            FROM trusted_public_keys
            {where}
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": row[0],
            "label": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "fingerprint_sha256": row[4],
            "pem_text": row[5],
            "active": bool(row[6]),
        }
        for row in rows
    ]


def remove_trusted_key(db_path: str | Path, key_id: int) -> None:
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            "SELECT fingerprint_sha256, label FROM trusted_public_keys WHERE id=?",
            (int(key_id),),
        ).fetchone()
        conn.execute(
            "UPDATE trusted_public_keys SET active=0, updated_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), int(key_id)),
        )
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="trust",
        action="remove-key",
        entity_type="trusted_public_key",
        entity_id=key_id,
        details={
            "fingerprint_sha256": row[0] if row else None,
            "label": row[1] if row else None,
        },
    )


def verify_signature_with_trust_store(
    db_path: str | Path,
    *,
    manifest_path: str | Path,
    signature_path: str | Path,
) -> dict[str, Any]:
    keys = list_trusted_keys(db_path)
    if not keys:
        return {
            "ok": False,
            "reason": "No trusted public keys are registered.",
            "attempts": [],
        }

    attempts = []
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for item in keys:
            public_path = tmp / f"trusted-{item['id']}.pem"
            public_path.write_text(item["pem_text"], encoding="utf-8")
            try:
                report = verify_release_signature(
                    manifest_path,
                    signature_path,
                    public_path,
                )
            except Exception as exc:
                report = {"ok": False, "error": str(exc)}

            report["trusted_key_id"] = item["id"]
            report["trusted_key_label"] = item["label"]
            report["trusted_key_fingerprint_sha256"] = item["fingerprint_sha256"]
            attempts.append(report)

            if report.get("ok"):
                return {
                    "ok": True,
                    "trusted_key": item,
                    "verification": report,
                    "attempts": attempts,
                }

    return {
        "ok": False,
        "reason": "Signature did not verify against any trusted key.",
        "attempts": attempts,
    }


__all__ = [
    "add_trusted_key",
    "list_trusted_keys",
    "remove_trusted_key",
    "verify_signature_with_trust_store",
]
