import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_campaign import (
    add_campaign_member,
    campaign_snapshot,
    create_campaign,
    export_campaign_bundle,
    import_campaign_bundle,
    list_campaigns,
    remove_campaign_member,
)
from hadron_jobs import run_job_spec
from hadron_storage import migrate_database
from hadron_studies import create_study, get_study, normalize_study_spec, set_study_status
from hadron_version import SCHEMA_VERSION


class CampaignTests(unittest.TestCase):
    def _make_study(self, db):
        spec = normalize_study_spec(
            {
                "events": 40,
                "energies": [1000, 6500],
                "presets": ["STANDARD"],
                "seed": 2000,
                "repeats": 1,
                "workers": 1,
            }
        )
        sid = create_study(db, name="Campaign Study", spec=spec)
        set_study_status(db, sid, "RUNNING")
        set_study_status(db, sid, "COMPLETE", result=run_job_spec(spec, workers=1))
        return sid

    def test_schema_v10(self):
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
            self.assertIn("campaigns", tables)
            self.assertIn("campaign_members", tables)

    def test_campaign_study_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            sid = self._make_study(db)
            cid = create_campaign(db, name="Physics Campaign")
            add_campaign_member(
                db,
                cid,
                member_type="study",
                member_id=sid,
                label="reference",
            )

            payload = campaign_snapshot(db, cid)
            self.assertEqual(payload["summary"]["studies"], 1)
            self.assertEqual(payload["summary"]["study_jobs"], 2)
            self.assertEqual(payload["members"][0]["label"], "reference")
            self.assertTrue(payload["members"][0]["resolved"])

    def test_member_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            sid = self._make_study(db)
            cid = create_campaign(db, name="Remove Test")
            add_campaign_member(
                db,
                cid,
                member_type="study",
                member_id=sid,
            )
            remove_campaign_member(
                db,
                cid,
                member_type="study",
                member_id=sid,
            )
            payload = campaign_snapshot(db, cid)
            self.assertEqual(payload["summary"]["members"], 0)

    def test_campaign_export_import_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_db = root / "source.db"
            dest_db = root / "dest.db"
            migrate_database(source_db)
            migrate_database(dest_db)

            sid = self._make_study(source_db)
            cid = create_campaign(
                source_db,
                name="Portable Campaign",
                description="portable test",
            )
            add_campaign_member(
                source_db,
                cid,
                member_type="study",
                member_id=sid,
            )

            bundle = root / "campaign.hadron-campaign.zip"
            export_campaign_bundle(source_db, cid, bundle)

            with zipfile.ZipFile(bundle) as z:
                self.assertIn("campaign.json", z.namelist())

            imported_id = import_campaign_bundle(
                dest_db,
                bundle,
                restore_studies=True,
            )
            imported = campaign_snapshot(dest_db, imported_id)

            self.assertEqual(imported["summary"]["studies"], 1)
            self.assertEqual(imported["summary"]["study_jobs"], 2)
            self.assertIn("(imported)", imported["campaign"]["name"])

    def test_list_campaigns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            first = create_campaign(db, name="One")
            second = create_campaign(db, name="Two")
            rows = list_campaigns(db)
            self.assertEqual(rows[0]["id"], second)
            self.assertEqual(rows[1]["id"], first)


if __name__ == "__main__":
    unittest.main()
