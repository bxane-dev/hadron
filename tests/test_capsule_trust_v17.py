import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from hadron_capsule import (
    create_study_capsule,
    inspect_capsule,
    list_capsules,
    register_capsule,
)
from hadron_release import generate_signing_keypair, sign_release_manifest
from hadron_storage import migrate_database
from hadron_trust import (
    add_trusted_key,
    list_trusted_keys,
    remove_trusted_key,
    verify_signature_with_trust_store,
)
from hadron_version import SCHEMA_VERSION


def sample_study():
    return {
        "id": 17,
        "name": "Capsule Study",
        "status": "COMPLETE",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:10:00",
        "spec": {
            "events": 100,
            "energies": [6500.0],
            "presets": ["STANDARD"],
            "seed": 42,
            "repeats": 1,
            "workers": 1,
        },
        "result": {
            "version": "1.7.0",
            "jobs": 1,
            "workers": 1,
            "results": [{
                "job_index": 0,
                "repeat": 0,
                "preset": "STANDARD",
                "beam_energy_gev": 6500.0,
                "events_requested": 100,
                "seed": 42,
                "saved_count": 10,
                "discarded_count": 90,
                "higgs_count": 1,
                "acceptance_rate": 10.0,
                "l1_energy_threshold": 5000.0,
                "met_trigger_threshold": 500.0,
                "higgs_window_gev": 3.0,
                "noise_enabled": True,
                "noise_sigma": 0.9,
                "resolution_sigma": 0.012,
            }],
        },
    }


class CapsuleTrustTests(unittest.TestCase):
    def test_schema_v7_tables(self):
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
            self.assertIn("trusted_public_keys", tables)
            self.assertIn("study_capsules", tables)

    def test_capsule_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            path = root / "study.hadron-capsule.zip"
            created = create_study_capsule(
                path,
                study=sample_study(),
                db_path=db,
            )
            report = inspect_capsule(path)
            self.assertTrue(report["ok"])
            self.assertEqual(
                report["capsule_sha256"],
                created["capsule_sha256"],
            )
            self.assertTrue(report["provenance_ok"])

    def test_capsule_tamper_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            path = root / "study.hadron-capsule.zip"
            create_study_capsule(path, study=sample_study(), db_path=db)

            with zipfile.ZipFile(path, "a") as z:
                z.writestr("study.json", '{"tampered": true}')

            report = inspect_capsule(path)
            self.assertFalse(report["ok"])

    def test_capsule_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            capsule_id = register_capsule(
                db,
                study_id=None,
                capsule_name="test.zip",
                capsule_sha256="a" * 64,
                verified=True,
                manifest={"format": "hadron-study-capsule"},
            )
            rows = list_capsules(db)
            self.assertEqual(rows[0]["id"], capsule_id)
            self.assertTrue(rows[0]["verified"])

    def test_trust_store_and_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "hadron.db"
            migrate_database(db)
            private_key = root / "private.pem"
            public_key = root / "public.pem"
            manifest = root / "manifest.json"
            signature = root / "signature.json"

            manifest.write_text(
                json.dumps({"application": "Hadron", "version": "1.7.0"}),
                encoding="utf-8",
            )
            generate_signing_keypair(private_key, public_key)
            sign_release_manifest(manifest, private_key, signature)

            key_id = add_trusted_key(
                db,
                label="Release Key",
                public_key_path=public_key,
            )
            report = verify_signature_with_trust_store(
                db,
                manifest_path=manifest,
                signature_path=signature,
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["trusted_key"]["id"], key_id)

            remove_trusted_key(db, key_id)
            self.assertEqual(list_trusted_keys(db), [])


if __name__ == "__main__":
    unittest.main()
