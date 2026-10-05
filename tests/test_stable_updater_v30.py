import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hadron_update import (
    expected_asset_names,
    parse_release_payload,
    select_release_assets,
    verify_downloaded_release,
)
from hadron_update_trust import (
    PUBLIC_KEY_FINGERPRINT_SHA256,
    PUBLIC_KEY_PEM,
)
from hadron_version import (
    CAMPAIGN_FORMAT_VERSION,
    CAPSULE_FORMAT_VERSION,
    PIPELINE_FORMAT_VERSION,
    PROJECT_FORMAT_VERSION,
    PUBLISHER_GITHUB,
    PUBLISHER_NAME,
    RECOVERY_BUNDLE_VERSION,
    RELEASE_MANIFEST_VERSION,
    RELEASE_REPOSITORY,
    RELEASE_SIGNATURE_VERSION,
    SCHEMA_VERSION,
    WORKSPACE_BUNDLE_VERSION,
    __version__,
)


class StableUpdaterTests(unittest.TestCase):
    def test_stable_identity(self):
        self.assertEqual(__version__, "3.0.0")
        self.assertEqual(SCHEMA_VERSION, 12)
        self.assertEqual(PUBLISHER_NAME, "bxane")
        self.assertEqual(PUBLISHER_GITHUB, "bxane-dev")
        self.assertEqual(RELEASE_REPOSITORY, "bxane-dev/hadron")

    def test_stable_format_versions(self):
        self.assertEqual(PROJECT_FORMAT_VERSION, 1)
        self.assertEqual(WORKSPACE_BUNDLE_VERSION, 1)
        self.assertEqual(RELEASE_MANIFEST_VERSION, 1)
        self.assertEqual(RELEASE_SIGNATURE_VERSION, 1)
        self.assertEqual(CAPSULE_FORMAT_VERSION, 1)
        self.assertEqual(CAMPAIGN_FORMAT_VERSION, 1)
        self.assertEqual(PIPELINE_FORMAT_VERSION, 1)
        self.assertEqual(RECOVERY_BUNDLE_VERSION, 1)

    def test_release_asset_selection(self):
        names = expected_asset_names("3.1.0")
        payload = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v3.1.0",
            "html_url": "https://github.com/bxane-dev/hadron/releases/tag/v3.1.0",
            "assets": [
                {
                    "name": name,
                    "browser_download_url": (
                        "https://github.com/bxane-dev/hadron/releases/download/"
                        f"v3.1.0/{name}"
                    ),
                    "size": 123,
                }
                for name in names.values()
            ],
        }
        release = parse_release_payload(payload)
        assets = select_release_assets(release)
        self.assertEqual(
            assets["installer"]["name"],
            "Hadron-Setup-v3.1.0.exe",
        )

    def test_pinned_public_key_fingerprint(self):
        key = serialization.load_pem_public_key(
            PUBLIC_KEY_PEM.encode("utf-8")
        )
        raw = key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            PUBLIC_KEY_FINGERPRINT_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
