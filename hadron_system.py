"""Maintenance and diagnostics helpers for Hadron v1.0."""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


def backup_database(source: str | Path, destination: str | Path) -> Path:
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return destination


def restore_database(source: str | Path, destination: str | Path) -> Path:
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)

    check = sqlite3.connect(source)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise ValueError("Source database failed SQLite integrity_check")
    finally:
        check.close()

    temp = destination.with_suffix(destination.suffix + ".restore_tmp")
    temp.unlink(missing_ok=True)

    src = sqlite3.connect(source)
    dst = sqlite3.connect(temp)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()

    os.replace(temp, destination)
    return destination


def database_diagnostics(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }
    if not path.exists():
        result["integrity"] = "missing"
        return result

    conn = sqlite3.connect(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        result["integrity"] = integrity[0] if integrity else "unknown"

        tables = {}
        for table in [
            "runs",
            "accepted_events",
            "bookmarks",
            "experiments",
            "analysis_projects",
            "study_jobs",
            "study_templates",
            "audit_log",
            "trusted_public_keys",
            "study_capsules",
            "reproduction_checks",
            "regression_runs",
            "campaigns",
            "campaign_members",
            "campaign_runs",
            "campaign_references",
            "pipelines",
            "pipeline_runs",
            "app_metadata",
        ]:
            try:
                tables[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.Error:
                tables[table] = None
        result["tables"] = tables

        version = conn.execute("PRAGMA user_version").fetchone()
        result["schema_version"] = int(version[0]) if version else 0
    finally:
        conn.close()
    return result


def environment_diagnostics() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable": sys.executable,
    }


def write_crash_log(
    directory: str | Path,
    exc_type,
    exc_value,
    exc_traceback,
) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"crash-{stamp}.log"
    content = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback)
    )
    path.write_text(
        "Hadron crash report\n"
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Environment: {json.dumps(environment_diagnostics(), sort_keys=True)}\n\n"
        + content,
        encoding="utf-8",
    )
    return path


__all__ = [
    "backup_database",
    "restore_database",
    "database_diagnostics",
    "environment_diagnostics",
    "write_crash_log",
]
