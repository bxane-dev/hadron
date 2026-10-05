"""Release verification and provenance helpers for Hadron v3.0 stable."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hadron_audit import verify_audit_chain
from hadron_study_compare import provenance_payload
from hadron_version import (
    PUBLISHER_GITHUB,
    PUBLISHER_NAME,
    RELEASE_MANIFEST_VERSION,
    RELEASE_REPOSITORY,
    RELEASE_SIGNATURE_VERSION,
    __version__,
)


_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?\s*$")


def parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(str(value))
    if not match:
        raise ValueError(f"Invalid semantic version: {value!r}")
    return tuple(int(group) for group in match.groups())


def compare_versions(current: str, candidate: str) -> int:
    """Return -1 if candidate is newer, 0 if equal, +1 if candidate is older."""
    current_tuple = parse_version(current)
    candidate_tuple = parse_version(candidate)
    if candidate_tuple > current_tuple:
        return -1
    if candidate_tuple < current_tuple:
        return 1
    return 0


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_hash(payload: Mapping[str, Any] | Sequence[Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_release_manifest(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    if payload.get("application") != "Hadron":
        raise ValueError("Release manifest is not for Hadron.")

    version = str(payload.get("version", ""))
    parse_version(version)

    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("Release manifest files must be a list.")

    normalized_files = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid release-manifest file entry.")
        name = str(item.get("name", "")).strip()
        checksum = str(item.get("sha256", "")).strip().lower()
        if not name or len(checksum) != 64:
            raise ValueError("Release-manifest file entry is incomplete.")
        normalized_files.append(
            {
                "name": name,
                "size_bytes": int(item.get("size_bytes", 0)),
                "sha256": checksum,
            }
        )

    normalized = dict(payload)
    normalized["manifest_version"] = int(
        payload.get("manifest_version", RELEASE_MANIFEST_VERSION)
    )
    normalized["version"] = version
    normalized["files"] = normalized_files
    return normalized


def verify_release_manifest(
    manifest_path: str | Path,
    *,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_release_manifest(manifest_path)
    base = Path(base_dir) if base_dir is not None else manifest_path.parent

    checks = []
    for item in manifest["files"]:
        file_path = base / item["name"]
        exists = file_path.exists()
        size = file_path.stat().st_size if exists else None
        actual_hash = sha256_file(file_path) if exists and file_path.is_file() else None
        size_ok = exists and size == item["size_bytes"]
        hash_ok = exists and actual_hash == item["sha256"]
        checks.append(
            {
                "name": item["name"],
                "path": str(file_path),
                "exists": bool(exists),
                "expected_size": item["size_bytes"],
                "actual_size": size,
                "size_ok": bool(size_ok),
                "expected_sha256": item["sha256"],
                "actual_sha256": actual_hash,
                "sha256_ok": bool(hash_ok),
                "ok": bool(size_ok and hash_ok),
            }
        )

    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "base_dir": str(base),
        "current_version": __version__,
        "version_comparison": compare_versions(__version__, manifest["version"]),
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


def release_status_text(comparison: int) -> str:
    if comparison < 0:
        return "NEWER RELEASE MANIFEST"
    if comparison > 0:
        return "OLDER RELEASE MANIFEST"
    return "CURRENT RELEASE"


def study_provenance_manifest(study: Mapping[str, Any]) -> dict[str, Any]:
    provenance = provenance_payload(study)
    result = study.get("result") or {}
    result_rows = result.get("results") or []

    job_hashes = [
        canonical_json_hash(row)
        for row in result_rows
    ]

    return {
        "format": "hadron-study-provenance",
        "format_version": 1,
        "application": "Hadron",
        "application_version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "study": provenance,
        "study_spec_sha256": canonical_json_hash(study.get("spec") or {}),
        "result_payload_sha256": canonical_json_hash(result),
        "job_result_sha256": job_hashes,
        "jobs": len(result_rows),
    }


def write_study_provenance(
    path: str | Path,
    study: Mapping[str, Any],
) -> Path:
    path = Path(path)
    payload = study_provenance_manifest(study)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path



def _public_key_raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_fingerprint(public_key_path: str | Path) -> str:
    public_key_path = Path(public_key_path)
    public_key = serialization.load_pem_public_key(
        public_key_path.read_bytes()
    )
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("Public key is not Ed25519.")
    return hashlib.sha256(_public_key_raw(public_key)).hexdigest()


def generate_signing_keypair(
    private_key_path: str | Path,
    public_key_path: str | Path,
) -> dict[str, Any]:
    private_key_path = Path(private_key_path)
    public_key_path = Path(public_key_path)

    if private_key_path.exists() or public_key_path.exists():
        raise FileExistsError("Refusing to overwrite an existing signing key.")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_bytes(private_bytes)
    public_key_path.write_bytes(public_bytes)

    return {
        "private_key": str(private_key_path),
        "public_key": str(public_key_path),
        "fingerprint_sha256": hashlib.sha256(
            _public_key_raw(public_key)
        ).hexdigest(),
    }


def sign_release_manifest(
    manifest_path: str | Path,
    private_key_path: str | Path,
    signature_path: str | Path,
) -> Path:
    manifest_path = Path(manifest_path)
    private_key_path = Path(private_key_path)
    signature_path = Path(signature_path)

    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(),
        password=None,
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Private key is not Ed25519.")

    manifest_bytes = manifest_path.read_bytes()
    signature = private_key.sign(manifest_bytes)
    public_key = private_key.public_key()

    payload = {
        "format": "hadron-release-signature",
        "format_version": RELEASE_SIGNATURE_VERSION,
        "algorithm": "Ed25519",
        "publisher": PUBLISHER_NAME,
        "publisher_github": PUBLISHER_GITHUB,
        "repository": RELEASE_REPOSITORY,
        "manifest_name": manifest_path.name,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "public_key_fingerprint_sha256": hashlib.sha256(
            _public_key_raw(public_key)
        ).hexdigest(),
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    signature_path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return signature_path


def verify_release_signature(
    manifest_path: str | Path,
    signature_path: str | Path,
    public_key_path: str | Path,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    signature_path = Path(signature_path)
    public_key_path = Path(public_key_path)

    payload = json.loads(signature_path.read_text(encoding="utf-8"))
    if payload.get("format") != "hadron-release-signature":
        raise ValueError("Not a Hadron release signature.")
    if int(payload.get("format_version", 0)) != RELEASE_SIGNATURE_VERSION:
        raise ValueError("Unsupported release signature version.")
    if payload.get("algorithm") != "Ed25519":
        raise ValueError("Unsupported release signature algorithm.")
    if payload.get("publisher") != PUBLISHER_NAME:
        raise ValueError("Release signature publisher mismatch.")
    if payload.get("publisher_github") != PUBLISHER_GITHUB:
        raise ValueError("Release signature GitHub publisher mismatch.")
    if payload.get("repository") != RELEASE_REPOSITORY:
        raise ValueError("Release signature repository mismatch.")

    public_key = serialization.load_pem_public_key(
        public_key_path.read_bytes()
    )
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("Public key is not Ed25519.")

    manifest_bytes = manifest_path.read_bytes()
    actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    actual_fingerprint = hashlib.sha256(
        _public_key_raw(public_key)
    ).hexdigest()

    hash_ok = actual_manifest_hash == payload.get("manifest_sha256")
    fingerprint_ok = (
        actual_fingerprint
        == payload.get("public_key_fingerprint_sha256")
    )

    signature_ok = False
    error = ""
    try:
        signature = base64.b64decode(payload["signature_base64"], validate=True)
        public_key.verify(signature, manifest_bytes)
        signature_ok = True
    except (InvalidSignature, ValueError, KeyError) as exc:
        error = str(exc) or exc.__class__.__name__

    return {
        "manifest_path": str(manifest_path),
        "signature_path": str(signature_path),
        "public_key_path": str(public_key_path),
        "manifest_sha256": actual_manifest_hash,
        "expected_manifest_sha256": payload.get("manifest_sha256"),
        "public_key_fingerprint_sha256": actual_fingerprint,
        "expected_public_key_fingerprint_sha256": payload.get(
            "public_key_fingerprint_sha256"
        ),
        "manifest_hash_ok": hash_ok,
        "fingerprint_ok": fingerprint_ok,
        "signature_ok": signature_ok,
        "error": error,
        "ok": bool(hash_ok and fingerprint_ok and signature_ok),
    }


def integrity_report(
    *,
    release_verification: Mapping[str, Any] | None = None,
    release_signature: Mapping[str, Any] | None = None,
    study: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    audit = verify_audit_chain(db_path) if db_path is not None else None
    study_provenance = (
        study_provenance_manifest(study) if study is not None else None
    )

    checks = []
    if release_verification is not None:
        checks.append(bool(release_verification.get("ok")))
    if release_signature is not None:
        checks.append(bool(release_signature.get("ok")))
    if audit is not None:
        checks.append(bool(audit.get("ok")))

    return {
        "format": "hadron-integrity-report",
        "format_version": 1,
        "application": "Hadron",
        "application_version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "release_verification": dict(release_verification)
        if release_verification is not None else None,
        "release_signature": dict(release_signature)
        if release_signature is not None else None,
        "study_provenance": study_provenance,
        "audit_chain": audit,
        "ok": all(checks) if checks else True,
    }


def write_integrity_report(
    path: str | Path,
    *,
    release_verification: Mapping[str, Any] | None = None,
    release_signature: Mapping[str, Any] | None = None,
    study: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> Path:
    path = Path(path)
    payload = integrity_report(
        release_verification=release_verification,
        release_signature=release_signature,
        study=study,
        db_path=db_path,
    )
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


__all__ = [
    "parse_version",
    "compare_versions",
    "sha256_file",
    "canonical_json_hash",
    "load_release_manifest",
    "verify_release_manifest",
    "release_status_text",
    "study_provenance_manifest",
    "write_study_provenance",
    "public_key_fingerprint",
    "generate_signing_keypair",
    "sign_release_manifest",
    "verify_release_signature",
    "integrity_report",
    "write_integrity_report",
]
