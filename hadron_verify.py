"""Hadron v3.0 standalone release verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_release import release_status_text, verify_release_manifest, verify_release_signature
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Verify Hadron release artifacts against a SHA-256 manifest."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Directory containing the files listed in the manifest.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON verification report.",
    )
    parser.add_argument(
        "--signature",
        type=Path,
        default=None,
        help="Optional Hadron release signature JSON.",
    )
    parser.add_argument(
        "--public-key",
        type=Path,
        default=None,
        help="Ed25519 public key PEM used to verify --signature.",
    )
    args = parser.parse_args()

    if (args.signature is None) != (args.public_key is None):
        parser.error("--signature and --public-key must be supplied together")

    report = verify_release_manifest(
        args.manifest,
        base_dir=args.base_dir,
    )

    manifest = report["manifest"]
    print(f"HadronVerify v{__version__}")
    print(f"Manifest release: {manifest['version']}")
    print(f"Version status: {release_status_text(report['version_comparison'])}")
    print("")

    for check in report["checks"]:
        marker = "OK" if check["ok"] else "FAIL"
        print(f"[{marker}] {check['name']}")
        if not check["ok"]:
            print(f"  expected: {check['expected_sha256']}")
            print(f"  actual:   {check['actual_sha256']}")

    signature_report = None
    if args.signature is not None:
        signature_report = verify_release_signature(
            args.manifest,
            args.signature,
            args.public_key,
        )
        report["signature_verification"] = signature_report
        report["ok"] = bool(report["ok"] and signature_report["ok"])
        print("")
        print(
            "[OK] Ed25519 signature"
            if signature_report["ok"]
            else "[FAIL] Ed25519 signature"
        )
        print(
            "Public-key fingerprint: "
            + signature_report["public_key_fingerprint_sha256"]
        )

    print("")
    print("VERIFIED" if report["ok"] else "VERIFICATION FAILED")

    if args.output is not None:
        args.output.write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )

    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
