import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hadron_update import verify_downloaded_release


class SignedUpdateTests(unittest.TestCase):
    def test_signature_and_installer_tamper_detection(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        fingerprint = hashlib.sha256(raw_public).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "Hadron-Setup-v3.0.0.exe"
            installer.write_bytes(b"synthetic installer fixture")

            manifest = root / "Hadron-v3.0.0-manifest.json"
            manifest_payload = {
                "application": "Hadron",
                "manifest_version": 1,
                "version": "3.0.0",
                "publisher": "bxane",
                "publisher_github": "bxane-dev",
                "repository": "bxane-dev/hadron",
                "files": [
                    {
                        "name": installer.name,
                        "size_bytes": installer.stat().st_size,
                        "sha256": hashlib.sha256(
                            installer.read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
            manifest.write_text(
                json.dumps(manifest_payload, indent=2),
                encoding="utf-8",
            )

            signature_bytes = private_key.sign(
                manifest.read_bytes()
            )
            signature = (
                root
                / "Hadron-v3.0.0-manifest.signature.json"
            )
            signature.write_text(
                json.dumps(
                    {
                        "format": "hadron-release-signature",
                        "format_version": 1,
                        "algorithm": "Ed25519",
                        "publisher": "bxane",
                        "publisher_github": "bxane-dev",
                        "repository": "bxane-dev/hadron",
                        "manifest_name": manifest.name,
                        "manifest_sha256": hashlib.sha256(
                            manifest.read_bytes()
                        ).hexdigest(),
                        "public_key_fingerprint_sha256": fingerprint,
                        "signature_base64": base64.b64encode(
                            signature_bytes
                        ).decode("ascii"),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch(
                "hadron_update.PUBLIC_KEY_PEM",
                public_pem,
            ), patch(
                "hadron_update.PUBLIC_KEY_FINGERPRINT_SHA256",
                fingerprint,
            ):
                result = verify_downloaded_release(
                    manifest_path=manifest,
                    signature_path=signature,
                    installer_path=installer,
                    expected_version="3.0.0",
                )
                self.assertTrue(result["ok"])

                installer.write_bytes(b"tampered installer")
                with self.assertRaises(ValueError):
                    verify_downloaded_release(
                        manifest_path=manifest,
                        signature_path=signature,
                        installer_path=installer,
                        expected_version="3.0.0",
                    )

    def test_wrong_signer_identity_is_rejected(self):
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        fingerprint = hashlib.sha256(raw_public).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installer = root / "Hadron-Setup-v3.0.0.exe"
            installer.write_bytes(b"x")
            manifest = root / "Hadron-v3.0.0-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "application": "Hadron",
                        "manifest_version": 1,
                        "version": "3.0.0",
                        "publisher": "bxane",
                        "publisher_github": "bxane-dev",
                        "repository": "bxane-dev/hadron",
                        "files": [
                            {
                                "name": installer.name,
                                "size_bytes": 1,
                                "sha256": hashlib.sha256(b"x").hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            signature_bytes = private_key.sign(
                manifest.read_bytes()
            )
            signature = root / "sig.json"
            signature.write_text(
                json.dumps(
                    {
                        "format": "hadron-release-signature",
                        "format_version": 1,
                        "algorithm": "Ed25519",
                        "publisher": "not-bxane",
                        "publisher_github": "bxane-dev",
                        "repository": "bxane-dev/hadron",
                        "manifest_sha256": hashlib.sha256(
                            manifest.read_bytes()
                        ).hexdigest(),
                        "public_key_fingerprint_sha256": fingerprint,
                        "signature_base64": base64.b64encode(
                            signature_bytes
                        ).decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "hadron_update.PUBLIC_KEY_PEM",
                public_pem,
            ), patch(
                "hadron_update.PUBLIC_KEY_FINGERPRINT_SHA256",
                fingerprint,
            ):
                with self.assertRaises(ValueError):
                    verify_downloaded_release(
                        manifest_path=manifest,
                        signature_path=signature,
                        installer_path=installer,
                        expected_version="3.0.0",
                    )


if __name__ == "__main__":
    unittest.main()
