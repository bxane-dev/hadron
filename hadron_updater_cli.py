"""Hadron v3.0 signed GitHub Releases updater."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hadron_update import (
    apply_latest_update,
    check_for_update,
    download_update,
)
from hadron_version import RELEASE_REPOSITORY, __version__


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Check and install signed Hadron releases from "
            f"https://github.com/{RELEASE_REPOSITORY}"
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--json", type=Path, default=None)

    download = sub.add_parser("download")
    download.add_argument("--dir", type=Path, required=True)
    download.add_argument("--json", type=Path, default=None)

    apply = sub.add_parser("apply")
    apply.add_argument("--dir", type=Path, default=None)
    apply.add_argument("--silent", action="store_true")
    apply.add_argument("--json", type=Path, default=None)

    args = parser.parse_args()
    print(f"HadronUpdater v{__version__}")
    print(f"Feed: https://github.com/{RELEASE_REPOSITORY}/releases")

    if args.command == "check":
        result = check_for_update()
        if args.json:
            args.json.write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )
        print(
            f"Current: {result['current_version']} · "
            f"Latest: {result['latest_version']}"
        )
        print(
            "UPDATE AVAILABLE"
            if result["update_available"]
            else "UP TO DATE"
        )
        return

    if args.command == "download":
        update = check_for_update()
        if not update["update_available"]:
            print("UP TO DATE")
            return
        result = download_update(
            update,
            destination_dir=args.dir,
        )
        if args.json:
            args.json.write_text(
                json.dumps(result, indent=2),
                encoding="utf-8",
            )
        print(result["installer_path"])
        return

    result = apply_latest_update(
        destination_dir=args.dir,
        silent=args.silent,
    )
    if args.json:
        args.json.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
