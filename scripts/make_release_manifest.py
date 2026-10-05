"""Generate a SHA-256 release manifest for Hadron artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hadron_version import (
    PUBLISHER_GITHUB,
    PUBLISHER_NAME,
    RELEASE_MANIFEST_VERSION,
    RELEASE_REPOSITORY,
    __version__,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    items = []
    for path in args.files:
        if not path.exists():
            continue
        items.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )

    payload = {
        "application": "Hadron",
        "manifest_version": RELEASE_MANIFEST_VERSION,
        "version": __version__,
        "publisher": PUBLISHER_NAME,
        "publisher_github": PUBLISHER_GITHUB,
        "repository": RELEASE_REPOSITORY,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
