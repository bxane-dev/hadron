import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_capsule import create_study_capsule
from hadron_jobs import run_job_spec
from hadron_regression import (
    check_regression_baseline,
    create_regression_baseline,
    discover_capsules,
    record_regression_run,
    report_to_csv,
    report_to_junit,
    run_capsule_regression,
)
from hadron_storage import migrate_database
from hadron_studies import normalize_study_spec
from hadron_version import SCHEMA_VERSION


def create_capsule(root: Path, name: str, seed: int) -> Path:
    db = root / "hadron.db"
    migrate_database(db)
    spec = normalize_study_spec(
        {
            "events": 60,
            "energies": [1000, 6500],
            "presets": ["STANDARD"],
            "seed": seed,
            "repeats": 1,
            "workers": 1,
        }
    )
    study = {
        "id": seed,
        "name": name,
        "status": "COMPLETE",
        "spec": spec,
        "result": run_job_spec(spec, workers=1),
    }
    path = root / f"{name}.hadron-capsule.zip"
    create_study_capsule(path, study=study, db_path=db)
    return path


class RegressionTests(unittest.TestCase):
    def test_discover_capsules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = create_capsule(root, "one", 10)
            nested = root / "nested"
            nested.mkdir()
            two = create_capsule(nested, "two", 20)
            found = discover_capsules([root])
            self.assertEqual(set(found), {one.resolve(), two.resolve()})

    def test_multi_capsule_regression_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_capsule(root, "one", 10)
            create_capsule(root, "two", 20)
            report = run_capsule_regression([root], workers_override=1)
            self.assertTrue(report["all_passed"])
            self.assertEqual(report["passed"], 2)
            self.assertEqual(report["capsules_checked"], 2)

    def test_baseline_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_capsule(root, "one", 10)
            baseline = create_regression_baseline([root])
            report = check_regression_baseline(
                baseline,
                [root],
                workers_override=1,
            )
            self.assertTrue(report["all_passed"])
            self.assertEqual(report["passed"], 1)

    def test_csv_and_junit_outputs(self):
        report = {
            "created_at": "2026-01-01T00:00:00",
            "rows": [
                {
                    "capsule_name": "ok.zip",
                    "study_name": "ok",
                    "status": "PASS",
                    "ok": True,
                },
                {
                    "capsule_name": "bad.zip",
                    "study_name": "bad",
                    "status": "DIVERGENCE",
                    "ok": False,
                    "failure_fields": ["saved_count"],
                },
            ],
        }
        csv_text = report_to_csv(report)
        junit = report_to_junit(report)
        self.assertIn("bad.zip", csv_text)
        self.assertIn("tests=\"2\"", junit)
        self.assertIn("failures=\"1\"", junit)

    def test_regression_run_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            report = {
                "capsules_checked": 2,
                "passed": 1,
                "diverged": 1,
                "invalid": 0,
                "errors": 0,
                "all_passed": False,
                "rows": [],
            }
            run_id = record_regression_run(db, report)
            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT passed, failed, all_passed FROM regression_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, (1, 1, 0))

    def test_schema_v9(self):
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
            self.assertIn("regression_runs", tables)


if __name__ == "__main__":
    unittest.main()
