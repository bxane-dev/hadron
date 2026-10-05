import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_jobs import run_job_spec
from hadron_storage import migrate_database
from hadron_studies import (
    create_study,
    get_study,
    list_studies,
    mark_running_studies_interrupted,
    normalize_study_spec,
    set_study_status,
    study_summary_text,
)
from hadron_version import SCHEMA_VERSION


class StudyTests(unittest.TestCase):
    def test_schema_v4_has_study_jobs(self):
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
            self.assertIn("study_jobs", tables)

    def test_normalize_study_spec(self):
        spec = normalize_study_spec(
            {
                "events": 100,
                "energies": [1000, 6500],
                "presets": ["STANDARD"],
                "seed": 3,
                "repeats": 2,
                "workers": 1,
            }
        )
        self.assertEqual(spec["events"], 100)
        self.assertEqual(spec["energies"], [1000.0, 6500.0])
        self.assertEqual(spec["repeats"], 2)

    def test_study_persistence_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            spec = {
                "events": 40,
                "energies": [1000, 6500],
                "presets": ["STANDARD"],
                "seed": 7,
                "repeats": 1,
                "workers": 1,
            }
            study_id = create_study(db, name="Test Study", spec=spec)
            set_study_status(db, study_id, "RUNNING")

            normalized = normalize_study_spec(spec)
            result = run_job_spec(normalized, workers=1)
            set_study_status(db, study_id, "COMPLETE", result=result)

            study = get_study(db, study_id)
            self.assertIsNotNone(study)
            self.assertEqual(study["status"], "COMPLETE")
            self.assertEqual(study["result"]["jobs"], 2)
            self.assertIn("Mean acceptance", study_summary_text(study))

    def test_interrupted_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            study_id = create_study(
                db,
                name="Interrupted",
                spec={
                    "events": 10,
                    "energies": [6500],
                    "presets": ["STANDARD"],
                    "workers": 1,
                },
            )
            set_study_status(db, study_id, "RUNNING")
            count = mark_running_studies_interrupted(db)
            self.assertEqual(count, 1)
            study = get_study(db, study_id)
            self.assertEqual(study["status"], "INTERRUPTED")

    def test_study_list_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            base = {
                "events": 10,
                "energies": [6500],
                "presets": ["STANDARD"],
                "workers": 1,
            }
            first = create_study(db, name="First", spec=base)
            second = create_study(db, name="Second", spec=base)
            studies = list_studies(db)
            self.assertEqual(studies[0]["id"], second)
            self.assertEqual(studies[1]["id"], first)


if __name__ == "__main__":
    unittest.main()
