import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_storage import migrate_database
from hadron_version import SCHEMA_VERSION
from hadron_study_compare import (
    clone_study_spec,
    compare_studies,
    delete_template,
    diff_specs,
    list_templates,
    save_template,
)


def make_study(study_id, name, acceptance, energy=6500, preset="STANDARD"):
    return {
        "id": study_id,
        "name": name,
        "status": "COMPLETE",
        "spec": {
            "events": 1000,
            "energies": [energy],
            "presets": [preset],
            "seed": 42,
            "repeats": 1,
            "workers": 1,
            "l1_threshold": 5000.0,
            "met_threshold": 500.0,
            "higgs_window": 3.0,
            "noise": True,
        },
        "result": {
            "version": "1.6.0",
            "jobs": 1,
            "workers": 1,
            "results": [
                {
                    "job_index": 0,
                    "repeat": 0,
                    "preset": preset,
                    "beam_energy_gev": energy,
                    "events_requested": 1000,
                    "seed": 42,
                    "saved_count": int(acceptance * 10),
                    "discarded_count": 1000 - int(acceptance * 10),
                    "higgs_count": 20,
                    "acceptance_rate": acceptance,
                    "l1_energy_threshold": 5000.0,
                    "met_trigger_threshold": 500.0,
                    "higgs_window_gev": 3.0,
                    "noise_enabled": True,
                    "noise_sigma": 0.9,
                    "resolution_sigma": 0.012,
                }
            ],
        },
    }


class StudyCompareTests(unittest.TestCase):
    def test_diff_specs(self):
        a = {"events": 100, "seed": 1}
        b = {"events": 200, "seed": 1}
        diff = diff_specs(a, b)
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0]["field"], "events")

    def test_compare_studies(self):
        a = make_study(1, "A", 10.0)
        b = make_study(2, "B", 8.0)
        comparison = compare_studies(a, b)
        self.assertEqual(len(comparison["grid"]), 1)
        self.assertAlmostEqual(
            comparison["grid"][0]["acceptance_delta_pp"],
            2.0,
        )

    def test_clone_spec(self):
        study = make_study(1, "A", 10.0)
        cloned = clone_study_spec(study, workers=2, seed=99)
        self.assertEqual(cloned["workers"], 2)
        self.assertEqual(cloned["seed"], 99)

    def test_template_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            study = make_study(1, "A", 10.0)
            template_id = save_template(
                db,
                name="Template A",
                spec=study["spec"],
            )
            templates = list_templates(db)
            self.assertEqual(templates[0]["id"], template_id)
            self.assertEqual(templates[0]["name"], "Template A")
            delete_template(db, template_id)
            self.assertEqual(list_templates(db), [])

    def test_schema_v5_has_templates(self):
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
            self.assertIn("study_templates", tables)


if __name__ == "__main__":
    unittest.main()
