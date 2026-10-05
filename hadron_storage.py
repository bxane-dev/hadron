"""Persistence, migration, portable-mode, and support-bundle helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import zipfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from hadron_system import backup_database, database_diagnostics, environment_diagnostics, restore_database
from hadron_version import SCHEMA_VERSION, WORKSPACE_BUNDLE_VERSION, __version__



def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Durably replace a text file without exposing a partially-written target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding=encoding)
    os.replace(temp, path)
    return path


def atomic_write_json(
    path: str | Path,
    payload: Any,
) -> Path:
    return atomic_write_text(
        path,
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def backup_configuration_file(
    source: str | Path,
    backup_dir: str | Path,
    *,
    label: str,
) -> Path | None:
    source = Path(source)
    if not source.exists():
        return None
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"{label}-{stamp}{source.suffix or '.json'}"
    shutil.copy2(source, destination)
    return destination


def validate_settings_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Hadron settings must be a JSON object.")
    return payload


def application_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_data_dir(
    *,
    env: Mapping[str, str] | None = None,
    app_dir: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    explicit = str(env.get("HADRON_DATA_DIR", "")).strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    base = Path(app_dir) if app_dir is not None else application_base_dir()
    if (base / "portable.flag").exists():
        return (base / "HadronData").resolve()

    home_path = Path(home) if home is not None else Path.home()
    return (home_path / ".hadron").resolve()


def _create_schema_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            target_energy_gev REAL NOT NULL,
            final_energy_gev REAL,
            collision_count INTEGER DEFAULT 0,
            saved_count INTEGER DEFAULT 0,
            discarded_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS accepted_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            event_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            detector TEXT NOT NULL,
            transverse_energy REAL NOT NULL,
            missing_energy REAL NOT NULL,
            muon_count INTEGER NOT NULL,
            particle_masses_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            is_higgs_candidate INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );

        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            note TEXT DEFAULT '',
            FOREIGN KEY(event_id) REFERENCES accepted_events(id)
        );

        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            preset TEXT NOT NULL,
            beam_energy_gev REAL NOT NULL,
            events_requested INTEGER NOT NULL,
            seed INTEGER NOT NULL,
            saved_count INTEGER NOT NULL,
            discarded_count INTEGER NOT NULL,
            higgs_count INTEGER NOT NULL,
            acceptance_rate REAL NOT NULL,
            summary_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analysis_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_accepted_events_run_id
            ON accepted_events(run_id);
        CREATE INDEX IF NOT EXISTS idx_accepted_events_timestamp
            ON accepted_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_experiments_created_at
            ON experiments(created_at);
        CREATE INDEX IF NOT EXISTS idx_projects_updated_at
            ON analysis_projects(updated_at);
        CREATE INDEX IF NOT EXISTS idx_bookmarks_event_id
            ON bookmarks(event_id);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS run_annotations (
            run_id INTEGER PRIMARY KEY,
            tags_json TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_run_annotations_archived
            ON run_annotations(archived);
        CREATE INDEX IF NOT EXISTS idx_runs_started_at
            ON runs(started_at);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS study_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            workers INTEGER NOT NULL DEFAULT 1,
            spec_json TEXT NOT NULL,
            result_json TEXT,
            error_text TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_study_jobs_status
            ON study_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_study_jobs_updated_at
            ON study_jobs(updated_at);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS study_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_study_id INTEGER,
            spec_json TEXT NOT NULL,
            FOREIGN KEY(source_study_id) REFERENCES study_jobs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_study_templates_updated_at
            ON study_templates(updated_at);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            previous_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE
        );

        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
            ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_log_entity
            ON audit_log(entity_type, entity_id);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trusted_public_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            fingerprint_sha256 TEXT NOT NULL UNIQUE,
            pem_text TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS study_capsules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            study_id INTEGER,
            created_at TEXT NOT NULL,
            capsule_name TEXT NOT NULL,
            capsule_sha256 TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            manifest_json TEXT NOT NULL,
            FOREIGN KEY(study_id) REFERENCES study_jobs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_trusted_keys_active
            ON trusted_public_keys(active);
        CREATE INDEX IF NOT EXISTS idx_study_capsules_study_id
            ON study_capsules(study_id);
        CREATE INDEX IF NOT EXISTS idx_study_capsules_created_at
            ON study_capsules(created_at);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reproduction_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            capsule_sha256 TEXT NOT NULL,
            source_study_id INTEGER,
            source_study_name TEXT,
            jobs_compared INTEGER NOT NULL,
            jobs_matching INTEGER NOT NULL,
            exact_reproduction INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reproduction_checks_created_at
            ON reproduction_checks(created_at);
        CREATE INDEX IF NOT EXISTS idx_reproduction_checks_capsule
            ON reproduction_checks(capsule_sha256);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS regression_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            capsules_checked INTEGER NOT NULL,
            passed INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            all_passed INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_regression_runs_created_at
            ON regression_runs(created_at);
        CREATE INDEX IF NOT EXISTS idx_regression_runs_all_passed
            ON regression_runs(all_passed);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS campaign_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            member_type TEXT NOT NULL,
            member_id INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            UNIQUE(campaign_id, member_type, member_id),
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );

        CREATE INDEX IF NOT EXISTS idx_campaigns_archived
            ON campaigns(archived);
        CREATE INDEX IF NOT EXISTS idx_campaign_members_campaign
            ON campaign_members(campaign_id);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            studies INTEGER NOT NULL,
            passed INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            all_passed INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS campaign_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reference_sha256 TEXT NOT NULL,
            member_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );

        CREATE INDEX IF NOT EXISTS idx_campaign_runs_campaign
            ON campaign_runs(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_campaign_runs_created_at
            ON campaign_runs(created_at);
        CREATE INDEX IF NOT EXISTS idx_campaign_references_campaign
            ON campaign_references(campaign_id);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )



def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipelines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            campaign_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id INTEGER NOT NULL,
            campaign_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            passed INTEGER NOT NULL DEFAULT 0,
            halted INTEGER NOT NULL DEFAULT 0,
            report_json TEXT NOT NULL,
            FOREIGN KEY(pipeline_id) REFERENCES pipelines(id),
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        );

        CREATE INDEX IF NOT EXISTS idx_pipelines_campaign
            ON pipelines(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline
            ON pipeline_runs(pipeline_id);
        """
    )
    conn.execute(
        """
        INSERT INTO app_metadata(key, value)
        VALUES('last_schema_migration_app_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (__version__,),
    )


def migrate_database(
    path: str | Path,
    *,
    backup_dir: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existed = path.exists() and path.stat().st_size > 0
    old_version = 0

    if existed:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            old_version = int(row[0]) if row else 0
        finally:
            conn.close()

    backup_path = None
    if existed and old_version < SCHEMA_VERSION:
        target_dir = (
            Path(backup_dir)
            if backup_dir is not None
            else path.parent / "migration_backups"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = (
            target_dir
            / f"hadron-pre-schema-{old_version}-to-{SCHEMA_VERSION}-{stamp}.sqlite3"
        )
        backup_database(path, backup_path)

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])

        if current == 0:
            _create_schema_v1(conn)
            conn.execute("PRAGMA user_version = 1")
            current = 1

        if current == 1:
            _migrate_v1_to_v2(conn)
            conn.execute("PRAGMA user_version = 2")
            current = 2

        if current == 2:
            _migrate_v2_to_v3(conn)
            conn.execute("PRAGMA user_version = 3")
            current = 3

        if current == 3:
            _migrate_v3_to_v4(conn)
            conn.execute("PRAGMA user_version = 4")
            current = 4

        if current == 4:
            _migrate_v4_to_v5(conn)
            conn.execute("PRAGMA user_version = 5")
            current = 5

        if current == 5:
            _migrate_v5_to_v6(conn)
            conn.execute("PRAGMA user_version = 6")
            current = 6

        if current == 6:
            _migrate_v6_to_v7(conn)
            conn.execute("PRAGMA user_version = 7")
            current = 7

        if current == 7:
            _migrate_v7_to_v8(conn)
            conn.execute("PRAGMA user_version = 8")
            current = 8

        if current == 8:
            _migrate_v8_to_v9(conn)
            conn.execute("PRAGMA user_version = 9")
            current = 9

        if current == 9:
            _migrate_v9_to_v10(conn)
            conn.execute("PRAGMA user_version = 10")
            current = 10

        if current == 10:
            _migrate_v10_to_v11(conn)
            conn.execute("PRAGMA user_version = 11")
            current = 11

        if current == 11:
            _migrate_v11_to_v12(conn)
            conn.execute("PRAGMA user_version = 12")
            current = 12

        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {current} is newer than this Hadron build "
                f"(supports {SCHEMA_VERSION})."
            )

        # A newly-created or migrated schema also records the app version.
        if current == SCHEMA_VERSION:
            conn.execute(
                """
                INSERT INTO app_metadata(key, value)
                VALUES('app_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (__version__,),
            )

        conn.commit()

        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError("Database failed SQLite integrity_check after migration")
    finally:
        conn.close()

    return {
        "old_version": old_version,
        "new_version": SCHEMA_VERSION,
        "backup_path": str(backup_path) if backup_path else None,
        "database_path": str(path),
    }


def save_session(path: str | Path, payload: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "format": "hadron-session",
        "version": 1,
        "app_version": __version__,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "state": dict(payload),
    }
    atomic_write_json(path, envelope)
    return path


def load_session(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "hadron-session":
        raise ValueError("Invalid Hadron session file")
    if int(payload.get("version", 0)) != 1:
        raise ValueError("Unsupported Hadron session version")
    state = payload.get("state", {})
    if not isinstance(state, dict):
        raise ValueError("Invalid session state")
    return state


def create_support_bundle(
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

    diagnostics = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "hadron_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "environment": environment_diagnostics(),
        "database": database_diagnostics(db_path),
        "data_dir": str(data_dir),
    }

    temp_db = None
    try:
        if db_path.exists():
            temp_db = data_dir / ".support-bundle-db.sqlite3"
            backup_database(db_path, temp_db)

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("diagnostics.json", json.dumps(diagnostics, indent=2))
            if settings_path.exists():
                z.write(settings_path, "settings.json")
            if session_path is not None and Path(session_path).exists():
                z.write(Path(session_path), "session.json")
            if temp_db is not None and temp_db.exists():
                z.write(temp_db, "hadron_runs.sqlite3")

            crash_dir = data_dir / "crashes"
            if crash_dir.exists():
                crash_files = sorted(
                    crash_dir.glob("*.log"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:5]
                for crash in crash_files:
                    z.write(crash, f"crashes/{crash.name}")

        return destination
    finally:
        if temp_db is not None:
            temp_db.unlink(missing_ok=True)



def export_workspace_bundle(
    destination: str | Path,
    *,
    data_dir: str | Path,
    db_path: str | Path,
    settings_path: str | Path,
    session_path: str | Path | None = None,
) -> Path:
    """Export a portable Hadron workspace bundle.

    The bundle contains a consistent SQLite backup plus non-secret local
    configuration/session files.
    """
    destination = Path(destination)
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    settings_path = Path(settings_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_db = data_dir / ".workspace-export.sqlite3"
    temp_db.unlink(missing_ok=True)
    try:
        if db_path.exists():
            backup_database(db_path, temp_db)

        manifest = {
            "format": "hadron-workspace-bundle",
            "bundle_version": WORKSPACE_BUNDLE_VERSION,
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            if temp_db.exists():
                z.write(temp_db, "workspace/hadron_runs.sqlite3")
            if settings_path.exists():
                z.write(settings_path, "workspace/settings.json")
            if session_path is not None and Path(session_path).exists():
                z.write(Path(session_path), "workspace/session.json")

        return destination
    finally:
        temp_db.unlink(missing_ok=True)


def inspect_workspace_bundle(path: str | Path) -> dict[str, Any]:
    """Validate and summarize a workspace bundle without importing it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            raise ValueError("Workspace bundle is missing manifest.json")
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != "hadron-workspace-bundle":
            raise ValueError("Not a Hadron workspace bundle")
        if int(manifest.get("bundle_version", 0)) != WORKSPACE_BUNDLE_VERSION:
            raise ValueError("Unsupported workspace bundle version")
        if "workspace/hadron_runs.sqlite3" not in names:
            raise ValueError("Workspace bundle is missing the database")

        return {
            "manifest": manifest,
            "has_settings": "workspace/settings.json" in names,
            "has_session": "workspace/session.json" in names,
            "entries": sorted(names),
        }


def import_workspace_bundle(
    path: str | Path,
    *,
    data_dir: str | Path,
    db_path: str | Path,
    settings_path: str | Path,
    session_path: str | Path | None = None,
) -> dict[str, Any]:
    """Safely import a full workspace bundle.

    The current database is backed up before replacement. The imported database
    is integrity-checked and migrated to the current schema before becoming active.
    """
    path = Path(path)
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    settings_path = Path(settings_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    info = inspect_workspace_bundle(path)
    import_dir = data_dir / ".workspace-import"
    if import_dir.exists():
        import shutil
        shutil.rmtree(import_dir)
    import_dir.mkdir(parents=True)

    safety_backup = None
    try:
        with zipfile.ZipFile(path) as z:
            z.extract("workspace/hadron_runs.sqlite3", import_dir)
            if info["has_settings"]:
                z.extract("workspace/settings.json", import_dir)
            if info["has_session"]:
                z.extract("workspace/session.json", import_dir)

        imported_db = import_dir / "workspace" / "hadron_runs.sqlite3"
        imported_settings = import_dir / "workspace" / "settings.json"
        imported_session = import_dir / "workspace" / "session.json"

        if imported_settings.exists():
            validate_settings_file(imported_settings)
        if imported_session.exists():
            load_session(imported_session)

        conn = sqlite3.connect(imported_db)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise ValueError("Imported workspace database failed integrity_check")
        finally:
            conn.close()

        # Upgrade the imported copy before it replaces the live DB.
        migrate_database(imported_db, backup_dir=import_dir / "migration_backups")

        config_backup_dir = data_dir / "recovery" / "pre-workspace-import"
        settings_backup = backup_configuration_file(
            settings_path,
            config_backup_dir,
            label="settings",
        )
        session_backup = (
            backup_configuration_file(
                session_path,
                config_backup_dir,
                label="session",
            )
            if session_path is not None
            else None
        )

        if db_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            safety_backup = data_dir / f"pre-workspace-import-{stamp}.sqlite3"
            backup_database(db_path, safety_backup)

        restore_database(imported_db, db_path)

        if imported_settings.exists():
            atomic_write_text(
                settings_path,
                imported_settings.read_text(encoding="utf-8"),
            )

        if session_path is not None and imported_session.exists():
            atomic_write_text(
                session_path,
                imported_session.read_text(encoding="utf-8"),
            )

        migration = migrate_database(
            db_path,
            backup_dir=data_dir / "migration_backups",
        )
        return {
            "manifest": info["manifest"],
            "safety_backup": str(safety_backup) if safety_backup else None,
            "settings_backup": (
                str(settings_backup) if settings_backup else None
            ),
            "session_backup": (
                str(session_backup) if session_backup else None
            ),
            "migration": migration,
        }
    finally:
        if import_dir.exists():
            import shutil
            shutil.rmtree(import_dir, ignore_errors=True)


__all__ = [
    "application_base_dir",
    "resolve_data_dir",
    "migrate_database",
    "save_session",
    "load_session",
    "create_support_bundle",
    "export_workspace_bundle",
    "inspect_workspace_bundle",
    "import_workspace_bundle",
]
