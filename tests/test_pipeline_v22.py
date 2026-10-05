import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_campaign import add_campaign_member, create_campaign
from hadron_campaign_run import save_campaign_reference
from hadron_jobs import run_job_spec
from hadron_pipeline import (
    create_pipeline,
    list_pipeline_runs,
    pipeline_to_junit,
    run_pipeline_to_database,
)
from hadron_storage import migrate_database
from hadron_studies import (
    create_study,
    normalize_study_spec,
    set_study_status,
)
from hadron_version import SCHEMA_VERSION


class PipelineTests(unittest.TestCase):
    def _setup(self, db):
        spec = normalize_study_spec(
            {
                "events": 50,
                "energies": [1000, 6500],
                "presets": ["STANDARD"],
                "seed": 2200,
                "repeats": 1,
                "workers": 1,
            }
        )
        study_id = create_study(
            db,
            name="Pipeline Study",
            spec=spec,
        )
        set_study_status(db, study_id, "RUNNING")
        set_study_status(
            db,
            study_id,
            "COMPLETE",
            result=run_job_spec(spec, workers=1),
        )

        campaign_id = create_campaign(
            db,
            name="Pipeline Campaign",
        )
        add_campaign_member(
            db,
            campaign_id,
            member_type="study",
            member_id=study_id,
            label="reference",
        )
        save_campaign_reference(db, campaign_id)
        return campaign_id

    def test_schema_v12(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite3"
            migrate_database(db)
            conn = sqlite3.connect(db)
            try:
                version = conn.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(version, 12)
            self.assertIn("pipelines", tables)
            self.assertIn("pipeline_runs", tables)

    def test_pipeline_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite3"
            migrate_database(db)
            campaign_id = self._setup(db)
            pipeline_id = create_pipeline(
                db,
                name="Release Gate",
                campaign_id=campaign_id,
            )
            report = run_pipeline_to_database(
                db,
                pipeline_id,
            )
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(
                report["database_run_id"],
                1,
            )
            self.assertEqual(
                list_pipeline_runs(db, pipeline_id)[0]["id"],
                report["database_run_id"],
            )

    def test_junit(self):
        xml = pipeline_to_junit(
            {
                "stages": [
                    {
                        "stage": "health",
                        "ok": True,
                        "skipped": False,
                    },
                    {
                        "stage": "run",
                        "ok": False,
                        "skipped": False,
                        "detail": {"x": 1},
                    },
                ]
            }
        )
        self.assertIn('tests="2"', xml)
        self.assertIn('failures="1"', xml)


if __name__ == "__main__":
    unittest.main()
