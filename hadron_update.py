"""Signed GitHub Releases auto-updater for Hadron v3.0 stable."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hadron_release import compare_versions, load_release_manifest, parse_version
from hadron_update_trust import (
    PUBLIC_KEY_FINGERPRINT_SHA256,
    PUBLIC_KEY_PEM,
)
from hadron_version import (
    PUBLISHER_GITHUB,
    PUBLISHER_NAME,
    RELEASE_API_URL,
    RELEASE_REPOSITORY,
    RELEASE_REPOSITORY_URL,
    __version__,
)


USER_AGENT = "Hadron-Updater/3.0 (+https://github.com/bxane-dev/hadron)"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024


def _request_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 20.0,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("Update download exceeds configured size limit.")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Update download exceeds configured size limit.")
    return data


def fetch_latest_release_payload(
    *,
    api_url: str = RELEASE_API_URL,
    timeout: float = 20.0,
) -> dict[str, Any]:
    data = _request_bytes(
        api_url,
        max_bytes=MAX_METADATA_BYTES,
        timeout=timeout,
    )
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub release response is invalid.")
    return payload


def parse_release_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if payload.get("draft"):
        raise ValueError("Latest release is a draft.")
    if payload.get("prerelease"):
        raise ValueError("Latest release is a prerelease.")

    tag = str(payload.get("tag_name", "")).strip()
    version = tag[1:] if tag.lower().startswith("v") else tag
    parse_version(version)

    assets = {}
    for asset in payload.get("assets") or []:
        if not isinstance(asset, Mapping):
            continue
        name = str(asset.get("name", "")).strip()
        url = str(asset.get("browser_download_url", "")).strip()
        if name and url.startswith("https://github.com/"):
            assets[name] = {
                "name": name,
                "url": url,
                "size": int(asset.get("size", 0) or 0),
            }

    return {
        "tag": tag,
        "version": version,
        "name": str(payload.get("name") or tag),
        "html_url": str(
            payload.get("html_url")
            or f"{RELEASE_REPOSITORY_URL}/releases/tag/{tag}"
        ),
        "published_at": payload.get("published_at"),
        "assets": assets,
    }


def expected_asset_names(version: str) -> dict[str, str]:
    parse_version(version)
    return {
        "installer": f"Hadron-Setup-v{version}.exe",
        "manifest": f"Hadron-v{version}-manifest.json",
        "signature": f"Hadron-v{version}-manifest.signature.json",
    }


def select_release_assets(
    release: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    names = expected_asset_names(str(release["version"]))
    available = release.get("assets") or {}
    selected = {}
    missing = []
    for kind, name in names.items():
        asset = available.get(name)
        if not asset:
            missing.append(name)
        else:
            selected[kind] = dict(asset)

    if missing:
        raise ValueError(
            "Release is missing required signed updater assets: "
            + ", ".join(missing)
        )
    return selected


def check_for_update(
    *,
    current_version: str = __version__,
    timeout: float = 20.0,
) -> dict[str, Any]:
    payload = fetch_latest_release_payload(timeout=timeout)
    release = parse_release_payload(payload)
    comparison = compare_versions(
        current_version,
        release["version"],
    )

    result = {
        "repository": RELEASE_REPOSITORY,
        "publisher": PUBLISHER_NAME,
        "publisher_github": PUBLISHER_GITHUB,
        "current_version": current_version,
        "latest_version": release["version"],
        "update_available": comparison < 0,
        "release": release,
    }

    if result["update_available"]:
        result["selected_assets"] = select_release_assets(release)
    else:
        result["selected_assets"] = {}

    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_signature(
    manifest_path: Path,
    signature_path: Path,
) -> dict[str, Any]:
    payload = json.loads(
        signature_path.read_text(encoding="utf-8")
    )
    if payload.get("format") != "hadron-release-signature":
        raise ValueError("Not a Hadron release signature.")
    if payload.get("algorithm") != "Ed25519":
        raise ValueError("Unsupported updater signature algorithm.")
    if payload.get("publisher") != PUBLISHER_NAME:
        raise ValueError("Updater publisher mismatch.")
    if payload.get("publisher_github") != PUBLISHER_GITHUB:
        raise ValueError("Updater GitHub publisher mismatch.")
    if payload.get("repository") != RELEASE_REPOSITORY:
        raise ValueError("Updater repository mismatch.")

    key = serialization.load_pem_public_key(
        PUBLIC_KEY_PEM.encode("utf-8")
    )
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Pinned updater key is not Ed25519.")

    raw_key = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(raw_key).hexdigest()
    if fingerprint != PUBLIC_KEY_FINGERPRINT_SHA256:
        raise ValueError("Pinned updater public-key fingerprint mismatch.")
    if (
        payload.get("public_key_fingerprint_sha256")
        != PUBLIC_KEY_FINGERPRINT_SHA256
    ):
        raise ValueError("Release was not signed by the pinned bxane key.")

    manifest_bytes = manifest_path.read_bytes()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if payload.get("manifest_sha256") != manifest_hash:
        raise ValueError("Signed manifest hash mismatch.")

    try:
        signature = base64.b64decode(
            payload["signature_base64"],
            validate=True,
        )
        key.verify(signature, manifest_bytes)
    except (InvalidSignature, ValueError, KeyError) as exc:
        raise ValueError("Release signature verification failed.") from exc

    return {
        "ok": True,
        "fingerprint_sha256": fingerprint,
        "publisher": PUBLISHER_NAME,
        "publisher_github": PUBLISHER_GITHUB,
        "repository": RELEASE_REPOSITORY,
    }


def _download_asset(
    asset: Mapping[str, Any],
    destination: Path,
    *,
    max_bytes: int,
) -> Path:
    data = _request_bytes(
        str(asset["url"]),
        max_bytes=max_bytes,
        timeout=60.0,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination


def verify_downloaded_release(
    *,
    manifest_path: str | Path,
    signature_path: str | Path,
    installer_path: str | Path,
    expected_version: str,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    signature_path = Path(signature_path)
    installer_path = Path(installer_path)

    signature = _verify_signature(
        manifest_path,
        signature_path,
    )
    manifest = load_release_manifest(manifest_path)

    if manifest["version"] != expected_version:
        raise ValueError("Downloaded manifest version mismatch.")
    if manifest.get("publisher") != PUBLISHER_NAME:
        raise ValueError("Release manifest publisher mismatch.")
    if manifest.get("publisher_github") != PUBLISHER_GITHUB:
        raise ValueError("Release manifest GitHub publisher mismatch.")
    if manifest.get("repository") != RELEASE_REPOSITORY:
        raise ValueError("Release manifest repository mismatch.")

    expected_name = expected_asset_names(expected_version)["installer"]
    if installer_path.name != expected_name:
        raise ValueError("Downloaded installer filename mismatch.")

    entry = next(
        (
            item
            for item in manifest["files"]
            if item["name"] == expected_name
        ),
        None,
    )
    if entry is None:
        raise ValueError("Signed manifest does not include the installer.")

    actual_size = installer_path.stat().st_size
    actual_hash = _sha256_file(installer_path)

    if actual_size != int(entry["size_bytes"]):
        raise ValueError("Installer size does not match signed manifest.")
    if actual_hash != entry["sha256"]:
        raise ValueError("Installer SHA-256 does not match signed manifest.")

    return {
        "ok": True,
        "version": expected_version,
        "installer": str(installer_path),
        "installer_sha256": actual_hash,
        "signature": signature,
    }


def download_update(
    update: Mapping[str, Any],
    *,
    destination_dir: str | Path,
) -> dict[str, Any]:
    if not update.get("update_available"):
        raise ValueError("No newer Hadron release is available.")

    version = str(update["latest_version"])
    assets = dict(update["selected_assets"])
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    installer_name = expected_asset_names(version)["installer"]
    manifest_name = expected_asset_names(version)["manifest"]
    signature_name = expected_asset_names(version)["signature"]

    manifest_path = _download_asset(
        assets["manifest"],
        destination_dir / manifest_name,
        max_bytes=MAX_METADATA_BYTES,
    )
    signature_path = _download_asset(
        assets["signature"],
        destination_dir / signature_name,
        max_bytes=MAX_METADATA_BYTES,
    )
    installer_path = _download_asset(
        assets["installer"],
        destination_dir / installer_name,
        max_bytes=MAX_INSTALLER_BYTES,
    )

    verification = verify_downloaded_release(
        manifest_path=manifest_path,
        signature_path=signature_path,
        installer_path=installer_path,
        expected_version=version,
    )

    return {
        "version": version,
        "installer_path": str(installer_path),
        "manifest_path": str(manifest_path),
        "signature_path": str(signature_path),
        "verification": verification,
    }


def launch_installer(
    installer_path: str | Path,
    *,
    silent: bool = False,
) -> int:
    if platform.system().lower() != "windows":
        raise RuntimeError("Hadron installer updates are supported on Windows.")

    installer_path = Path(installer_path)
    if not installer_path.exists():
        raise FileNotFoundError(installer_path)

    args = [str(installer_path)]
    if silent:
        args += [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
        ]
    else:
        args += ["/CLOSEAPPLICATIONS"]

    process = subprocess.Popen(
        args,
        close_fds=True,
        creationflags=getattr(
            subprocess,
            "DETACHED_PROCESS",
            0,
        ),
    )
    return int(process.pid)


def apply_latest_update(
    *,
    destination_dir: str | Path | None = None,
    silent: bool = False,
) -> dict[str, Any]:
    update = check_for_update()
    if not update["update_available"]:
        return {
            "updated": False,
            "reason": "already-current",
            "current_version": __version__,
            "latest_version": update["latest_version"],
        }

    if destination_dir is None:
        destination_dir = (
            Path(tempfile.gettempdir())
            / "Hadron"
            / "updates"
            / update["latest_version"]
        )

    downloaded = download_update(
        update,
        destination_dir=destination_dir,
    )
    pid = launch_installer(
        downloaded["installer_path"],
        silent=silent,
    )
    return {
        "updated": True,
        "installer_pid": pid,
        **downloaded,
    }


__all__ = [
    "fetch_latest_release_payload",
    "parse_release_payload",
    "expected_asset_names",
    "select_release_assets",
    "check_for_update",
    "verify_downloaded_release",
    "download_update",
    "launch_installer",
    "apply_latest_update",
]
