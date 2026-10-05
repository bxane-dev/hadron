"""Hadron v3.0 release publication preflight."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from hadron_update import expected_asset_names
from hadron_update_trust import PUBLIC_KEY_FINGERPRINT_SHA256
from hadron_version import (
    PUBLISHER_GITHUB,
    PUBLISHER_NAME,
    RELEASE_REPOSITORY,
    RELEASE_REPOSITORY_URL,
    __version__,
)


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def local_preflight(repo_root: str | Path) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append(
            {
                "name": name,
                "ok": bool(ok),
                "detail": detail,
            }
        )

    add(
        "stable-version",
        __version__ == "3.0.0",
        f"version={__version__}",
    )
    add(
        "publisher",
        PUBLISHER_NAME == "bxane"
        and PUBLISHER_GITHUB == "bxane-dev",
        f"{PUBLISHER_NAME} ({PUBLISHER_GITHUB})",
    )
    add(
        "repository",
        RELEASE_REPOSITORY == "bxane-dev/hadron",
        RELEASE_REPOSITORY,
    )

    required = [
        ".github/workflows/windows-build.yml",
        "installer/Hadron.iss",
        "HadronUpdater.spec",
        "hadron_update.py",
        "hadron_update_trust.py",
        "release-public.pem",
        "STABLE_FORMATS.md",
        "RELEASE_SIGNING.md",
        "PUBLISHING.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
    ]
    for relative in required:
        path = repo_root / relative
        add(
            f"file:{relative}",
            path.is_file(),
            "present" if path.is_file() else "missing",
        )

    private_pems = [
        path
        for path in repo_root.rglob("*.pem")
        if "private" in path.name.lower()
    ]
    add(
        "no-private-key-in-repo",
        not private_pems,
        (
            "none"
            if not private_pems
            else ", ".join(str(x.relative_to(repo_root)) for x in private_pems)
        ),
    )

    code, head = _git(["rev-parse", "HEAD"], repo_root)
    add("git-head", code == 0, head)

    code, tag = _git(
        ["rev-list", "-n", "1", "v3.0.0"],
        repo_root,
    )
    add(
        "tag-v3.0.0",
        code == 0 and tag == head,
        f"tag={tag} head={head}",
    )

    code, status = _git(["status", "--porcelain"], repo_root)
    add(
        "clean-working-tree",
        code == 0 and not status,
        status or "clean",
    )

    code, tracked = _git(["ls-files"], repo_root)
    tracked_private = [
        line
        for line in tracked.splitlines()
        if line.lower().endswith(".pem")
        and "private" in line.lower()
    ]
    add(
        "private-key-not-tracked",
        code == 0 and not tracked_private,
        ", ".join(tracked_private) or "none",
    )

    workflow_text = (
        repo_root / ".github/workflows/windows-build.yml"
    ).read_text(encoding="utf-8")
    add(
        "signing-secret-required",
        "HADRON_RELEASE_SIGNING_KEY_B64" in workflow_text,
        "workflow requires secret",
    )
    add(
        "signature-release-asset",
        "manifest.signature.json" in workflow_text,
        "signature artifact configured",
    )
    add(
        "updater-release-asset",
        "HadronUpdater.exe" in workflow_text,
        "updater artifact configured",
    )

    expected = expected_asset_names(__version__)
    add(
        "updater-installer-name",
        expected["installer"] == "Hadron-Setup-v3.0.0.exe",
        expected["installer"],
    )
    add(
        "pinned-key-fingerprint",
        len(PUBLIC_KEY_FINGERPRINT_SHA256) == 64,
        PUBLIC_KEY_FINGERPRINT_SHA256,
    )

    return {
        "format": "hadron-release-preflight",
        "version": __version__,
        "repository": RELEASE_REPOSITORY,
        "repository_url": RELEASE_REPOSITORY_URL,
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


def remote_repository_check(
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{RELEASE_REPOSITORY}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Hadron-Release-Preflight/3.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "ok": True,
            "status": 200,
            "full_name": payload.get("full_name"),
            "private": payload.get("private"),
            "default_branch": payload.get("default_branch"),
            "html_url": payload.get("html_url"),
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "reason": str(exc.reason),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }


def main():
    parser = argparse.ArgumentParser(
        description="Validate Hadron v3 stable release publication readiness."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Also verify that bxane-dev/hadron exists through GitHub's public API.",
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    result = local_preflight(args.repo_root)
    if args.remote:
        result["remote"] = remote_repository_check()
        result["ok"] = result["ok"] and result["remote"]["ok"]

    if args.json:
        args.json.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )

    for check in result["checks"]:
        print(
            f"[{'PASS' if check['ok'] else 'FAIL'}] "
            f"{check['name']}: {check['detail']}"
        )

    if args.remote:
        remote = result["remote"]
        print(
            f"[{'PASS' if remote['ok'] else 'FAIL'}] "
            f"remote-repository: {remote}"
        )

    print("PREFLIGHT PASS" if result["ok"] else "PREFLIGHT FAIL")
    raise SystemExit(0 if result["ok"] else 9)


if __name__ == "__main__":
    main()
