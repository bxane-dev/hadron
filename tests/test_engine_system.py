import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from hadron_engine import (
    SimulationConfig,
    evaluate_event,
    generate_event,
    run_counts,
)
from hadron_jobs import expand_job_spec, run_job_spec
from hadron_system import (
    backup_database,
    database_diagnostics,
    restore_database,
)
import random
import sqlite3


class EngineTests(unittest.TestCase):
    def test_deterministic_counts(self):
        config = SimulationConfig(events=500, seed=999, preset="STANDARD")
        self.assertEqual(run_counts(config), run_counts(config))

    def test_different_seed_changes_counts_eventually(self):
        a = run_counts(SimulationConfig(events=1000, seed=1))
        b = run_counts(SimulationConfig(events=1000, seed=2))
        self.assertNotEqual(
            (a["saved_count"], a["higgs_count"]),
            (b["saved_count"], b["higgs_count"]),
        )

    def test_generate_event_detector_override(self):
        event = generate_event(
            random.Random(1),
            6500,
            "STANDARD",
            detector="ATLAS-SIM",
        )
        self.assertEqual(event["detector"], "ATLAS-SIM")

    def test_evaluate_event_shape(self):
        event = {
            "transverse_energy": 9000.0,
            "missing_energy": 0.0,
            "muon_count": 0,
            "particle_masses": [125.0],
        }
        result = evaluate_event(
            event,
            l1_threshold=5000,
            met_threshold=500,
            higgs_window=3,
        )
        self.assertTrue(result["saved"])
        self.assertTrue(result["higgs"])


class JobTests(unittest.TestCase):
    def test_expand_job_spec(self):
        jobs = expand_job_spec({
            "events": 10,
            "energies": [1000, 6500],
            "presets": ["STANDARD", "HIGGS STUDY"],
            "repeats": 2,
            "seed": 10,
        })
        self.assertEqual(len(jobs), 8)
        self.assertEqual([job["job_index"] for job in jobs], list(range(8)))

    def test_job_spec_serial(self):
        payload = run_job_spec(
            {
                "events": 50,
                "energies": [1000, 6500],
                "preset": "STANDARD",
                "seed": 7,
            },
            workers=1,
        )
        self.assertEqual(payload["version"], "3.0.0")
        self.assertEqual(payload["jobs"], 2)


class SystemTests(unittest.TestCase):
    def test_database_backup_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.db"
            backup = tmp / "backup.db"
            restored = tmp / "restored.db"

            with closing(sqlite3.connect(source)) as conn:
                conn.execute("CREATE TABLE t (x INTEGER)")
                conn.execute("INSERT INTO t VALUES (7)")
                conn.commit()

            backup_database(source, backup)
            restore_database(backup, restored)

            with closing(sqlite3.connect(restored)) as conn:
                value = conn.execute("SELECT x FROM t").fetchone()[0]
            self.assertEqual(value, 7)

    def test_database_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "diag.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE runs (id INTEGER)")
                conn.commit()
            diag = database_diagnostics(path)
            self.assertEqual(diag["integrity"], "ok")
            self.assertEqual(diag["tables"]["runs"], 0)


if __name__ == "__main__":
    unittest.main()
