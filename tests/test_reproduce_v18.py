import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_capsule import create_study_capsule
from hadron_jobs import run_job_spec
from hadron_reproduce import (
    list_reproduction_checks,
    reproduce_capsule,
    reproduce_capsule_to_database,
    restore_capsule_study,
    stable_job_hash,
)
from hadron_storage import migrate_database
from hadron_studies import get_study, normalize_study_spec
from hadron_version import SCHEMA_VERSION


def make_study():
    spec = normalize_study_spec(
        {
            "events": 120,
            "energies": [1000, 6500],
            "presets": ["STANDARD"],
            "seed": 180,
            "repeats": 1,
            "workers": 1,
        }
    )
    result = run_job_spec(spec, workers=1)
    return {
        "id": 8,
        "name": "Repro Study",
        "status": "COMPLETE",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:01:00",
        "spec": spec,
        "result": result,
    }


class ReproduceTests(unittest.TestCase):
    def test_stable_hash_ignores_created_at(self):
        a = {"created_at": "a", "x": 1, "nested": {"y": 2}}
        b = {"created_at": "b", "x": 1, "nested": {"y": 2}}
        self.assertEqual(stable_job_hash(a), stable_job_hash(b))

    def test_exact_capsule_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            capsule = root / "study.hadron-capsule.zip"
            create_study_capsule(
                capsule,
                study=make_study(),
                db_path=db,
            )
            report = reproduce_capsule(capsule)
            self.assertTrue(report["exact_reproduction"])
            self.assertEqual(
                report["jobs_matching"],
                report["jobs_compared"],
            )

    def test_reproduction_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            capsule = root / "study.hadron-capsule.zip"
            create_study_capsule(
                capsule,
                study=make_study(),
                db_path=db,
            )
            report = reproduce_capsule_to_database(db, capsule)
            self.assertTrue(report["exact_reproduction"])
            rows = list_reproduction_checks(db)
            self.assertEqual(rows[0]["id"], report["database_check_id"])
            self.assertTrue(rows[0]["exact_reproduction"])

    def test_restore_capsule_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            capsule = root / "study.hadron-capsule.zip"
            original = make_study()
            create_study_capsule(
                capsule,
                study=original,
                db_path=db,
            )
            new_id = restore_capsule_study(db, capsule)
            restored = get_study(db, new_id)
            self.assertEqual(restored["status"], "COMPLETE")
            self.assertEqual(restored["spec"], original["spec"])
            self.assertEqual(
                restored["result"]["results"],
                original["result"]["results"],
            )

    def test_schema_v8(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
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
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("reproduction_checks", tables)


if __name__ == "__main__":
    unittest.main()
