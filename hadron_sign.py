"""Hadron v3.0 Ed25519 release signing utility."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_release import (
    generate_signing_keypair,
    public_key_fingerprint,
    sign_release_manifest,
)
from hadron_version import __version__


def main():
    parser = argparse.ArgumentParser(
        description="Generate Ed25519 keys and sign Hadron release manifests."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="Generate a new Ed25519 keypair.")
    keygen.add_argument("--private", type=Path, required=True)
    keygen.add_argument("--public", type=Path, required=True)

    sign = sub.add_parser("sign", help="Sign a release manifest.")
    sign.add_argument("manifest", type=Path)
    sign.add_argument("--private", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)

    fingerprint = sub.add_parser(
        "fingerprint",
        help="Print an Ed25519 public-key fingerprint.",
    )
    fingerprint.add_argument("public_key", type=Path)

    args = parser.parse_args()

    print(f"HadronSign v{__version__}")

    if args.command == "keygen":
        result = generate_signing_keypair(args.private, args.public)
        print(json.dumps(result, indent=2))
        return

    if args.command == "sign":
        output = sign_release_manifest(
            args.manifest,
            args.private,
            args.output,
        )
        print(output)
        return

    if args.command == "fingerprint":
        print(public_key_fingerprint(args.public_key))
        return


if __name__ == "__main__":
    main()
