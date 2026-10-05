"""Portable reproducibility capsules for Hadron v1.7."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from hadron_db import connect_db
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_audit import append_audit, verify_audit_chain
from hadron_release import canonical_json_hash, study_provenance_manifest
from hadron_studies import get_study
from hadron_study_analysis import create_study_report_package
from hadron_version import CAPSULE_FORMAT_VERSION, __version__


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_study_capsule(
    destination: str | Path,
    *,
    study: Mapping[str, Any],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    provenance = study_provenance_manifest(study)
    audit = verify_audit_chain(db_path) if db_path is not None else None

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        study_path = tmp / "study.json"
        provenance_path = tmp / "provenance.json"
        audit_path = tmp / "audit-proof.json"
        report_path = tmp / "study-report.zip"

        study_path.write_text(json.dumps(dict(study), indent=2), encoding="utf-8")
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
        create_study_report_package(report_path, study)

        payload_files = [study_path, provenance_path, audit_path, report_path]
        entries = [
            {
                "name": item.name,
                "size_bytes": item.stat().st_size,
                "sha256": _sha256_file(item),
            }
            for item in payload_files
        ]

        capsule_manifest = {
            "format": "hadron-study-capsule",
            "format_version": CAPSULE_FORMAT_VERSION,
            "application": "Hadron",
            "application_version": __version__,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "study_id": study.get("id"),
            "study_name": study.get("name"),
            "study_spec_sha256": canonical_json_hash(study.get("spec") or {}),
            "result_payload_sha256": canonical_json_hash(study.get("result") or {}),
            "audit_head_hash": (audit or {}).get("head_hash"),
            "files": entries,
        }

        manifest_bytes = json.dumps(
            capsule_manifest,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("capsule.json", manifest_bytes)
            for item in payload_files:
                z.write(item, item.name)

    capsule_sha256 = _sha256_file(destination)
    return {
        "path": str(destination),
        "capsule_sha256": capsule_sha256,
        "manifest": capsule_manifest,
    }


def inspect_capsule(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "capsule.json" not in names:
            raise ValueError("Capsule is missing capsule.json")

        manifest = json.loads(z.read("capsule.json").decode("utf-8"))
        if manifest.get("format") != "hadron-study-capsule":
            raise ValueError("Not a Hadron study capsule.")
        if int(manifest.get("format_version", 0)) != CAPSULE_FORMAT_VERSION:
            raise ValueError("Unsupported Hadron capsule format version.")

        checks = []
        for item in manifest.get("files", []):
            name = str(item.get("name", ""))
            if (
                not name
                or "/" in name
                or "\\" in name
                or name.startswith(".")
                or ".." in name
            ):
                raise ValueError("Capsule contains an unsafe file name.")

            exists = name in names
            data = z.read(name) if exists else b""
            actual_size = len(data) if exists else None
            actual_hash = _sha256_bytes(data) if exists else None
            size_ok = exists and actual_size == int(item.get("size_bytes", -1))
            hash_ok = exists and actual_hash == item.get("sha256")
            checks.append(
                {
                    "name": name,
                    "exists": exists,
                    "expected_size": item.get("size_bytes"),
                    "actual_size": actual_size,
                    "expected_sha256": item.get("sha256"),
                    "actual_sha256": actual_hash,
                    "size_ok": bool(size_ok),
                    "sha256_ok": bool(hash_ok),
                    "ok": bool(size_ok and hash_ok),
                }
            )

        study = (
            json.loads(z.read("study.json").decode("utf-8"))
            if "study.json" in names
            else None
        )
        provenance = (
            json.loads(z.read("provenance.json").decode("utf-8"))
            if "provenance.json" in names
            else None
        )

    provenance_ok = False
    if study is not None and provenance is not None:
        study_hash = canonical_json_hash(study.get("spec") or {})
        result_hash = canonical_json_hash(study.get("result") or {})
        provenance_ok = (
            provenance.get("study_spec_sha256") == study_hash
            and provenance.get("result_payload_sha256") == result_hash
            and manifest.get("study_spec_sha256") == study_hash
            and manifest.get("result_payload_sha256") == result_hash
        )

    return {
        "path": str(path),
        "capsule_sha256": _sha256_file(path),
        "manifest": manifest,
        "checks": checks,
        "provenance_ok": provenance_ok,
        "ok": all(item["ok"] for item in checks) and provenance_ok,
    }


def register_capsule(
    db_path: str | Path,
    *,
    study_id: int | None,
    capsule_name: str,
    capsule_sha256: str,
    verified: bool,
    manifest: Mapping[str, Any],
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO study_capsules(
                study_id, created_at, capsule_name, capsule_sha256,
                verified, manifest_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                study_id,
                now,
                str(capsule_name),
                str(capsule_sha256),
                1 if verified else 0,
                json.dumps(dict(manifest), sort_keys=True),
            ),
        )
        capsule_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    append_audit(
        db_path,
        category="capsule",
        action="register",
        entity_type="study_capsule",
        entity_id=capsule_id,
        details={
            "study_id": study_id,
            "capsule_name": capsule_name,
            "capsule_sha256": capsule_sha256,
            "verified": bool(verified),
        },
    )
    return capsule_id


def list_capsules(
    db_path: str | Path,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = connect_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, study_id, created_at, capsule_name,
                   capsule_sha256, verified, manifest_json
            FROM study_capsules
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
            "study_id": row[1],
            "created_at": row[2],
            "capsule_name": row[3],
            "capsule_sha256": row[4],
            "verified": bool(row[5]),
            "manifest": json.loads(row[6]),
        }
        for row in rows
    ]


def create_capsule_from_database(
    db_path: str | Path,
    study_id: int,
    destination: str | Path,
) -> dict[str, Any]:
    study = get_study(db_path, study_id)
    if study is None:
        raise ValueError(f"Study #{study_id} was not found.")
    if not study.get("result"):
        raise ValueError("Only completed studies with results can be sealed.")

    created = create_study_capsule(
        destination,
        study=study,
        db_path=db_path,
    )
    verified = inspect_capsule(destination)
    created["verification"] = verified
    created["registry_id"] = register_capsule(
        db_path,
        study_id=study_id,
        capsule_name=Path(destination).name,
        capsule_sha256=created["capsule_sha256"],
        verified=verified["ok"],
        manifest=created["manifest"],
    )
    return created


__all__ = [
    "create_study_capsule",
    "inspect_capsule",
    "register_capsule",
    "list_capsules",
    "create_capsule_from_database",
]
