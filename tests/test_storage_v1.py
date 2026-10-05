import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_doctor import run_doctor
from hadron_storage import (
    create_support_bundle,
    load_session,
    migrate_database,
    resolve_data_dir,
    save_session,
)
from hadron_version import SCHEMA_VERSION


class StorageTests(unittest.TestCase):
    def test_data_dir_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = resolve_data_dir(
                env={"HADRON_DATA_DIR": tmp},
                app_dir=Path(tmp) / "app",
                home=Path(tmp) / "home",
            )
            self.assertEqual(result, Path(tmp).resolve())

    def test_portable_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "portable.flag").write_text("", encoding="utf-8")
            result = resolve_data_dir(env={}, app_dir=base, home=base / "home")
            self.assertEqual(result, (base / "HadronData").resolve())

    def test_default_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = resolve_data_dir(env={}, app_dir=home / "app", home=home)
            self.assertEqual(result, (home / ".hadron").resolve())

    def test_migration_from_unversioned_v09_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"

            # Reproduce the essential v0.9 schema with user_version still at 0.
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
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = migrate_database(db, backup_dir=root / "backups")
            self.assertEqual(result["new_version"], SCHEMA_VERSION)
            self.assertTrue(result["backup_path"])

            conn = sqlite3.connect(db)
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("app_metadata", tables)
            self.assertIn("experiments", tables)
            self.assertIn("analysis_projects", tables)

    def test_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            save_session(path, {"target_energy": 6500, "plot_mode": "MASS"})
            state = load_session(path)
            self.assertEqual(state["target_energy"], 6500)
            self.assertEqual(state["plot_mode"], "MASS")

    def test_support_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            settings = root / "settings.json"
            session = root / "session.json"
            migrate_database(db)
            settings.write_text('{"x": 1}', encoding="utf-8")
            save_session(session, {"target_energy": 6500})
            bundle = root / "support.zip"

            create_support_bundle(
                bundle,
                data_dir=root,
                db_path=db,
                settings_path=settings,
                session_path=session,
            )
            with zipfile.ZipFile(bundle) as z:
                names = set(z.namelist())

            self.assertIn("diagnostics.json", names)
            self.assertIn("settings.json", names)
            self.assertIn("session.json", names)
            self.assertIn("hadron_runs.sqlite3", names)

    def test_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_doctor(Path(tmp))
            self.assertTrue(report["ok"])
            self.assertTrue(report["checks"]["engine_deterministic"])
            self.assertTrue(report["checks"]["database_integrity"])
            self.assertEqual(report["database"]["schema_version"], SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
