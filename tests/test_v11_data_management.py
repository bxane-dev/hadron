import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_storage import (
    export_workspace_bundle,
    import_workspace_bundle,
    inspect_workspace_bundle,
    migrate_database,
)
from hadron_version import SCHEMA_VERSION


class V11DataManagementTests(unittest.TestCase):
    def test_schema_v3_has_run_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            result = migrate_database(db)
            self.assertEqual(result["new_version"], SCHEMA_VERSION)
            conn = sqlite3.connect(db)
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("run_annotations", tables)

    def test_v2_to_v3_migration_preserves_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            conn = sqlite3.connect(db)
            try:
                conn.executescript(
                    """
                    CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        target_energy_gev REAL NOT NULL,
                        final_energy_gev REAL,
                        collision_count INTEGER DEFAULT 0,
                        saved_count INTEGER DEFAULT 0,
                        discarded_count INTEGER DEFAULT 0
                    );
                    CREATE TABLE accepted_events (
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
                        is_higgs_candidate INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE bookmarks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id INTEGER NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        note TEXT DEFAULT ''
                    );
                    CREATE TABLE experiments (
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
                    CREATE TABLE analysis_projects (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE TABLE app_metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    INSERT INTO runs(started_at, target_energy_gev)
                    VALUES('2026-01-01T00:00:00', 6500);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = migrate_database(db, backup_dir=root / "backups")
            self.assertTrue(result["backup_path"])
            conn = sqlite3.connect(db)
            try:
                run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(run_count, 1)
            self.assertEqual(version, SCHEMA_VERSION)

    def test_workspace_bundle_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            dest_dir = root / "dest"
            source_dir.mkdir()
            dest_dir.mkdir()

            source_db = source_dir / "hadron_runs.db"
            source_settings = source_dir / "settings.json"
            source_session = source_dir / "session.json"
            migrate_database(source_db)

            conn = sqlite3.connect(source_db)
            try:
                conn.execute(
                    "INSERT INTO runs(started_at, target_energy_gev) VALUES(?, ?)",
                    ("2026-01-01T00:00:00", 6500),
                )
                conn.commit()
            finally:
                conn.close()

            source_settings.write_text('{"mode":"source"}', encoding="utf-8")
            source_session.write_text(
                '{"format":"hadron-session","version":1,"state":{"target_energy":6500}}',
                encoding="utf-8",
            )

            bundle = root / "workspace.hadron-workspace.zip"
            export_workspace_bundle(
                bundle,
                data_dir=source_dir,
                db_path=source_db,
                settings_path=source_settings,
                session_path=source_session,
            )

            info = inspect_workspace_bundle(bundle)
            self.assertEqual(info["manifest"]["format"], "hadron-workspace-bundle")

            dest_db = dest_dir / "hadron_runs.db"
            dest_settings = dest_dir / "settings.json"
            dest_session = dest_dir / "session.json"
            migrate_database(dest_db)

            result = import_workspace_bundle(
                bundle,
                data_dir=dest_dir,
                db_path=dest_db,
                settings_path=dest_settings,
                session_path=dest_session,
            )
            self.assertIn("migration", result)

            conn = sqlite3.connect(dest_db)
            try:
                runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(runs, 1)
            self.assertIn("source", dest_settings.read_text(encoding="utf-8"))

    def test_invalid_workspace_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("manifest.json", json.dumps({"format": "other"}))
            with self.assertRaises(ValueError):
                inspect_workspace_bundle(path)


if __name__ == "__main__":
    unittest.main()
