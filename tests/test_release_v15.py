import json
import tempfile
import unittest
from pathlib import Path

from hadron_release import (
    canonical_json_hash,
    compare_versions,
    load_release_manifest,
    parse_version,
    study_provenance_manifest,
    verify_release_manifest,
)


class ReleaseTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertEqual(parse_version("v1.5.0"), (1, 5, 0))
        self.assertEqual(compare_versions("1.5.0", "1.5.0"), 0)
        self.assertEqual(compare_versions("1.5.0", "1.6.0"), -1)
        self.assertEqual(compare_versions("1.5.0", "1.4.9"), 1)

    def test_manifest_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "Hadron.exe"
            artifact.write_bytes(b"hadron-test-binary")
            import hashlib
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

            manifest = {
                "application": "Hadron",
                "manifest_version": 1,
                "version": "1.6.0",
                "files": [
                    {
                        "name": "Hadron.exe",
                        "size_bytes": artifact.stat().st_size,
                        "sha256": digest,
                    }
                ],
            }
            manifest_path = root / "Hadron-v1.5.0-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = verify_release_manifest(manifest_path)
            self.assertTrue(report["ok"])
            self.assertTrue(report["checks"][0]["sha256_ok"])

    def test_manifest_detects_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "Hadron.exe"
            artifact.write_bytes(b"original")
            import hashlib
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "application": "Hadron",
                        "version": "1.6.0",
                        "files": [{
                            "name": "Hadron.exe",
                            "size_bytes": artifact.stat().st_size,
                            "sha256": digest,
                        }],
                    }
                ),
                encoding="utf-8",
            )

            artifact.write_bytes(b"tampered")
            report = verify_release_manifest(manifest_path)
            self.assertFalse(report["ok"])

    def test_provenance_manifest(self):
        study = {
            "id": 7,
            "name": "Repro",
            "status": "COMPLETE",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:01:00",
            "spec": {"events": 100, "seed": 42},
            "result": {
                "version": "1.6.0",
                "jobs": 1,
                "workers": 1,
                "results": [{"acceptance_rate": 10.0}],
            },
        }
        payload = study_provenance_manifest(study)
        self.assertEqual(payload["format"], "hadron-study-provenance")
        self.assertEqual(payload["jobs"], 1)
        self.assertEqual(len(payload["study_spec_sha256"]), 64)
        self.assertEqual(len(payload["job_result_sha256"][0]), 64)

    def test_canonical_hash_order_independent(self):
        self.assertEqual(
            canonical_json_hash({"a": 1, "b": 2}),
            canonical_json_hash({"b": 2, "a": 1}),
        )


if __name__ == "__main__":
    unittest.main()
