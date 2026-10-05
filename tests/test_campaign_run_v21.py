import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_campaign import add_campaign_member, create_campaign
from hadron_campaign_run import (
    campaign_health,
    check_campaign_reference,
    create_campaign_report_bundle,
    list_campaign_runs,
    list_campaign_references,
    run_campaign_to_database,
    save_campaign_reference,
)
from hadron_jobs import run_job_spec
from hadron_storage import migrate_database
from hadron_studies import create_study, normalize_study_spec, set_study_status
from hadron_version import SCHEMA_VERSION


class CampaignRunTests(unittest.TestCase):
    def _campaign_with_study(self, db):
        spec = normalize_study_spec(
            {
                "events": 80,
                "energies": [1000, 6500],
                "presets": ["STANDARD"],
                "seed": 2100,
                "repeats": 1,
                "workers": 1,
            }
        )
        sid = create_study(db, name="Campaign Run Study", spec=spec)
        set_study_status(db, sid, "RUNNING")
        set_study_status(
            db,
            sid,
            "COMPLETE",
            result=run_job_spec(spec, workers=1),
        )

        cid = create_campaign(db, name="Executable Campaign")
        add_campaign_member(
            db,
            cid,
            member_type="study",
            member_id=sid,
            label="reference",
        )
        return cid, sid

    def test_schema_v11(self):
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
            self.assertIn("campaign_runs", tables)
            self.assertIn("campaign_references", tables)

    def test_campaign_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            cid, _ = self._campaign_with_study(db)
            health = campaign_health(db, cid)
            self.assertTrue(health["healthy"])
            self.assertEqual(health["healthy_members"], 1)

    def test_campaign_deterministic_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            cid, _ = self._campaign_with_study(db)

            report = run_campaign_to_database(
                db,
                cid,
                workers_override=1,
            )
            self.assertTrue(report["all_passed"])
            self.assertEqual(report["passed"], 1)
            self.assertGreaterEqual(report["database_run_id"], 1)

            runs = list_campaign_runs(db, cid)
            self.assertEqual(runs[0]["id"], report["database_run_id"])
            self.assertTrue(runs[0]["all_passed"])

    def test_campaign_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            cid, _ = self._campaign_with_study(db)

            reference_id = save_campaign_reference(
                db,
                cid,
                name="Reference A",
            )
            refs = list_campaign_references(db, cid)
            self.assertEqual(refs[0]["id"], reference_id)

            check = check_campaign_reference(
                db,
                refs[0]["payload"],
            )
            self.assertTrue(check["matches"])
            self.assertEqual(check["drifted_members"], 0)

    def test_campaign_report_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            cid, _ = self._campaign_with_study(db)
            run_campaign_to_database(db, cid)
            save_campaign_reference(db, cid)

            report_zip = root / "campaign-report.zip"
            create_campaign_report_bundle(
                db,
                cid,
                report_zip,
            )

            with zipfile.ZipFile(report_zip) as z:
                names = set(z.namelist())
                self.assertIn("report.html", names)
                self.assertIn("campaign.json", names)
                self.assertIn("health.json", names)
                self.assertIn("health.csv", names)
                self.assertIn("latest-campaign-run.json", names)
                self.assertIn("reference-check.json", names)


if __name__ == "__main__":
    unittest.main()
