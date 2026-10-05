import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hadron_audit import append_audit, verify_audit_chain
from hadron_release import (
    generate_signing_keypair,
    integrity_report,
    public_key_fingerprint,
    sign_release_manifest,
    verify_release_signature,
)
from hadron_storage import migrate_database
from hadron_version import SCHEMA_VERSION


class SigningAuditTests(unittest.TestCase):
    def test_ed25519_manifest_signature_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_key = root / "release-private.pem"
            public_key = root / "release-public.pem"
            manifest = root / "manifest.json"
            signature = root / "manifest.signature.json"

            manifest.write_text(
                json.dumps({"application": "Hadron", "version": "1.6.0"}),
                encoding="utf-8",
            )
            key_info = generate_signing_keypair(private_key, public_key)
            sign_release_manifest(manifest, private_key, signature)
            report = verify_release_signature(
                manifest,
                signature,
                public_key,
            )
            self.assertTrue(report["ok"])
            self.assertEqual(
                key_info["fingerprint_sha256"],
                public_key_fingerprint(public_key),
            )

    def test_signature_detects_manifest_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_key = root / "private.pem"
            public_key = root / "public.pem"
            manifest = root / "manifest.json"
            signature = root / "signature.json"

            manifest.write_text('{"version":"1.6.0"}', encoding="utf-8")
            generate_signing_keypair(private_key, public_key)
            sign_release_manifest(manifest, private_key, signature)
            manifest.write_text('{"version":"1.6.1"}', encoding="utf-8")

            report = verify_release_signature(
                manifest,
                signature,
                public_key,
            )
            self.assertFalse(report["ok"])
            self.assertFalse(report["manifest_hash_ok"])

    def test_audit_chain_roundtrip_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)

            append_audit(
                db,
                category="study",
                action="create",
                entity_type="study",
                entity_id=1,
                details={"x": 1},
            )
            append_audit(
                db,
                category="study",
                action="complete",
                entity_type="study",
                entity_id=1,
                details={"y": 2},
            )

            self.assertTrue(verify_audit_chain(db)["ok"])

            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "UPDATE audit_log SET details_json='{\"tampered\":true}' WHERE id=1"
                )
                conn.commit()
            finally:
                conn.close()

            self.assertFalse(verify_audit_chain(db)["ok"])

    def test_integrity_report_includes_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hadron.db"
            migrate_database(db)
            append_audit(
                db,
                category="test",
                action="one",
                details={},
            )
            report = integrity_report(
                release_verification={"ok": True},
                release_signature={"ok": True},
                study={
                    "id": 1,
                    "name": "Study",
                    "status": "COMPLETE",
                    "spec": {"seed": 42},
                    "result": {"results": []},
                },
                db_path=db,
            )
            self.assertTrue(report["ok"])
            self.assertTrue(report["audit_chain"]["ok"])
            self.assertEqual(
                report["format"],
                "hadron-integrity-report",
            )

    def test_schema_v6_has_audit_log(self):
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
            self.assertIn("audit_log", tables)


if __name__ == "__main__":
    unittest.main()
