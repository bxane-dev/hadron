import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_db import connect_db, initialize_runtime_pragmas
from hadron_recovery import (
    create_recovery_snapshot,
    inspect_recovery_snapshot,
    restore_recovery_snapshot,
)
from hadron_storage import (
    atomic_write_json,
    import_workspace_bundle,
    migrate_database,
    save_session,
    export_workspace_bundle,
)


class ReleaseHardeningTests(unittest.TestCase):
    def test_connect_db_enables_foreign_keys_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite3"
            migrate_database(db)
            conn = connect_db(db)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertGreaterEqual(
                    conn.execute("PRAGMA busy_timeout").fetchone()[0],
                    5000,
                )
            finally:
                conn.close()

    def test_runtime_pragmas_enable_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite3"
            migrate_database(db)
            values = initialize_runtime_pragmas(db)
            self.assertEqual(values["foreign_keys"], "1")
            self.assertEqual(values["journal_mode"].lower(), "wal")

    def test_recovery_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            db = data / "hadron_runs.db"
            settings = data / "settings.json"
            session = data / "session.json"
            migrate_database(db)
            atomic_write_json(settings, {"theme": "dark"})
            save_session(session, {"preset": "STANDARD"})

            conn = connect_db(db)
            try:
                conn.execute(
                    """
                    INSERT INTO runs(started_at, target_energy_gev)
                    VALUES('2026-01-01T00:00:00', 6500)
                    """
                )
                conn.commit()
            finally:
                conn.close()

            snapshot = root / "recovery.zip"
            create_recovery_snapshot(
                snapshot,
                data_dir=data,
                db_path=db,
                settings_path=settings,
                session_path=session,
            )
            self.assertTrue(inspect_recovery_snapshot(snapshot)["ok"])

            atomic_write_json(settings, {"theme": "light"})
            conn = connect_db(db)
            try:
                conn.execute("DELETE FROM runs")
                conn.commit()
            finally:
                conn.close()

            result = restore_recovery_snapshot(
                snapshot,
                data_dir=data,
                db_path=db,
                settings_path=settings,
                session_path=session,
            )
            self.assertTrue(result["restored"])
            self.assertTrue(Path(result["safety_snapshot"]).exists())
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["theme"],
                "dark",
            )

            conn = connect_db(db)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM runs"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 1)

    def test_workspace_import_rejects_bad_settings_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()

            source_db = source / "hadron_runs.db"
            target_db = target / "hadron_runs.db"
            source_settings = source / "settings.json"
            target_settings = target / "settings.json"
            source_session = source / "session.json"
            target_session = target / "session.json"

            migrate_database(source_db)
            migrate_database(target_db)
            atomic_write_json(source_settings, {"good": True})
            atomic_write_json(target_settings, {"keep": True})
            save_session(source_session, {"x": 1})
            save_session(target_session, {"x": 2})

            bundle = root / "workspace.zip"
            export_workspace_bundle(
                bundle,
                data_dir=source,
                db_path=source_db,
                settings_path=source_settings,
                session_path=source_session,
            )

            # Rewrite settings entry as an invalid non-object JSON payload.
            import zipfile
            rewrite = root / "bad.zip"
            with zipfile.ZipFile(bundle) as zin, zipfile.ZipFile(
                rewrite,
                "w",
                zipfile.ZIP_DEFLATED,
            ) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "workspace/settings.json":
                        data = b"[1,2,3]"
                    zout.writestr(item, data)

            with self.assertRaises(ValueError):
                import_workspace_bundle(
                    rewrite,
                    data_dir=target,
                    db_path=target_db,
                    settings_path=target_settings,
                    session_path=target_session,
                )

            self.assertEqual(
                json.loads(
                    target_settings.read_text(encoding="utf-8")
                ),
                {"keep": True},
            )


if __name__ == "__main__":
    unittest.main()
