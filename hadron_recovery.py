"""Recovery snapshots for Hadron v2.4."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from hadron_storage import (
    atomic_write_text,
    backup_configuration_file,
    load_session,
    migrate_database,
    validate_settings_file,
)
from hadron_system import backup_database, restore_database
from hadron_version import RECOVERY_BUNDLE_VERSION, SCHEMA_VERSION, __version__


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_recovery_snapshot(
    destination: str | Path,
    *,
    data_dir: str | Path,
    db_path: str | Path,
    settings_path: str | Path,
    session_path: str | Path | None = None,
) -> Path:
    destination = Path(destination)
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    settings_path = Path(settings_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        files: list[tuple[str, Path]] = []

        if db_path.exists():
            db_copy = tmp / "hadron_runs.sqlite3"
            backup_database(db_path, db_copy)
            files.append(("recovery/hadron_runs.sqlite3", db_copy))

        if settings_path.exists():
            validate_settings_file(settings_path)
            files.append(("recovery/settings.json", settings_path))

        if session_path is not None and Path(session_path).exists():
            load_session(session_path)
            files.append(("recovery/session.json", Path(session_path)))

        file_manifest = []
        for archive_name, file_path in files:
            data = file_path.read_bytes()
            file_manifest.append(
                {
                    "name": archive_name,
                    "size_bytes": len(data),
                    "sha256": _sha256(data),
                }
            )

        manifest = {
            "format": "hadron-recovery-snapshot",
            "format_version": RECOVERY_BUNDLE_VERSION,
            "application": "Hadron",
            "application_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": file_manifest,
        }

        with zipfile.ZipFile(
            destination,
            "w",
            zipfile.ZIP_DEFLATED,
        ) as z:
            z.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2),
            )
            for archive_name, file_path in files:
                z.write(file_path, archive_name)

    return destination


def inspect_recovery_snapshot(
    path: str | Path,
) -> dict[str, Any]:
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("Recovery snapshot is missing manifest.json.")

        manifest = json.loads(
            z.read("manifest.json").decode("utf-8")
        )
        if manifest.get("format") != "hadron-recovery-snapshot":
            raise ValueError("Not a Hadron recovery snapshot.")
        if int(manifest.get("format_version", 0)) != RECOVERY_BUNDLE_VERSION:
            raise ValueError("Unsupported recovery snapshot version.")

        checks = []
        for item in manifest.get("files", []):
            name = str(item.get("name", ""))
            if (
                not name.startswith("recovery/")
                or ".." in name
                or "\\" in name
            ):
                raise ValueError("Unsafe recovery archive entry.")
            exists = name in names
            data = z.read(name) if exists else b""
            checks.append(
                {
                    "name": name,
                    "exists": exists,
                    "size_ok": (
                        exists
                        and len(data) == int(item.get("size_bytes", -1))
                    ),
                    "sha256_ok": (
                        exists
                        and _sha256(data) == item.get("sha256")
                    ),
                }
            )

        ok = bool(checks) and all(
            row["exists"]
            and row["size_ok"]
            and row["sha256_ok"]
            for row in checks
        )
        return {
            "path": str(path),
            "manifest": manifest,
            "checks": checks,
            "ok": ok,
        }


def restore_recovery_snapshot(
    path: str | Path,
    *,
    data_dir: str | Path,
    db_path: str | Path,
    settings_path: str | Path,
    session_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(path)
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    settings_path = Path(settings_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    inspection = inspect_recovery_snapshot(path)
    if not inspection["ok"]:
        raise ValueError("Recovery snapshot failed integrity verification.")

    safety_dir = data_dir / "recovery" / "pre-restore"
    safety_dir.mkdir(parents=True, exist_ok=True)
    safety_snapshot = safety_dir / (
        "pre-restore-"
        + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        + ".hadron-recovery.zip"
    )
    create_recovery_snapshot(
        safety_snapshot,
        data_dir=data_dir,
        db_path=db_path,
        settings_path=settings_path,
        session_path=session_path,
    )

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.startswith("recovery/"):
                    z.extract(name, tmp)

        imported_db = tmp / "recovery" / "hadron_runs.sqlite3"
        imported_settings = tmp / "recovery" / "settings.json"
        imported_session = tmp / "recovery" / "session.json"

        if imported_settings.exists():
            validate_settings_file(imported_settings)
        if imported_session.exists():
            load_session(imported_session)

        if imported_db.exists():
            migrate_database(
                imported_db,
                backup_dir=tmp / "migration_backups",
            )
            restore_database(imported_db, db_path)
            migrate_database(
                db_path,
                backup_dir=data_dir / "migration_backups",
            )

        if imported_settings.exists():
            backup_configuration_file(
                settings_path,
                safety_dir,
                label="settings",
            )
            atomic_write_text(
                settings_path,
                imported_settings.read_text(encoding="utf-8"),
            )

        if (
            session_path is not None
            and imported_session.exists()
        ):
            backup_configuration_file(
                session_path,
                safety_dir,
                label="session",
            )
            atomic_write_text(
                session_path,
                imported_session.read_text(encoding="utf-8"),
            )

    return {
        "restored": True,
        "snapshot": str(path),
        "safety_snapshot": str(safety_snapshot),
        "manifest": inspection["manifest"],
    }


__all__ = [
    "create_recovery_snapshot",
    "inspect_recovery_snapshot",
    "restore_recovery_snapshot",
]
