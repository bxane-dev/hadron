import random
import tempfile
import unittest
from pathlib import Path

from hadron_engine import (
    SimulationConfig,
    run_counts,
)
from hadron_jobs import run_job_spec
from hadron_run_state import RunCounters
from hadron_storage import migrate_database
from hadron_studies import create_study, get_study, normalize_study_spec
from hadron_workspace import detector_efficiency_matrix


class HardeningTests(unittest.TestCase):
    def test_run_counters_are_local_and_resettable(self):
        counters = RunCounters()
        counters.record_collision()
        counters.record_saved()
        self.assertEqual(counters.to_db_tuple(), (1, 1, 0))

        counters.reset()
        counters.record_collision()
        counters.record_discarded()
        self.assertEqual(counters.to_db_tuple(), (1, 0, 1))

    def test_custom_detector_response_is_deterministic(self):
        config = SimulationConfig(
            events=500,
            seed=23,
            noise_sigma=0.0,
            resolution_sigma=0.0,
        )
        a = run_counts(config)
        b = run_counts(config)
        self.assertEqual(a, b)

    def test_custom_detector_response_changes_job_payload(self):
        spec = {
            "events": 300,
            "energies": [6500],
            "presets": ["STANDARD"],
            "seed": 2300,
            "repeats": 1,
            "workers": 1,
            "noise": True,
            "noise_sigma": 0.0,
            "resolution_sigma": 0.0,
        }
        result = run_job_spec(spec, workers=1)
        row = result["results"][0]
        self.assertEqual(row["noise_sigma"], 0.0)
        self.assertEqual(row["resolution_sigma"], 0.0)

    def test_study_normalization_keeps_detector_response(self):
        normalized = normalize_study_spec(
            {
                "events": 100,
                "energies": [6500],
                "presets": ["STANDARD"],
                "noise_sigma": 1.75,
                "resolution_sigma": 0.031,
            }
        )
        self.assertEqual(normalized["noise_sigma"], 1.75)
        self.assertEqual(normalized["resolution_sigma"], 0.031)

    def test_direct_get_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            sid = create_study(
                db,
                name="Lookup",
                spec={
                    "events": 10,
                    "energies": [6500],
                    "presets": ["STANDARD"],
                },
            )
            study = get_study(db, sid)
            self.assertEqual(study["id"], sid)
            self.assertEqual(study["name"], "Lookup")
            self.assertIsNone(get_study(db, sid + 999))

    def test_detector_matrix_is_label_only_common_sample(self):
        matrix = detector_efficiency_matrix(
            events_per_detector=200,
            energy=6500,
            preset="STANDARD",
            seed=23,
        )
        values = list(matrix.values())
        self.assertTrue(values)
        for item in values[1:]:
            self.assertEqual(item, values[0])


if __name__ == "__main__":
    unittest.main()
