# Changelog

## 3.0.0 — Stable

Released by **bxane (bxane-dev)**.

- Declared the v3 stable format contract.
- Kept SQLite schema v12 and retained historical migrations.
- Added explicit historical schema migration regression coverage.
- Added signed GitHub Releases auto-update checks.
- Added Update Center to the desktop GUI.
- Added HadronUpdater CLI/EXE.
- Pinned a bxane Ed25519 public release key in the updater.
- Added publisher/repository identity to release manifests and signatures.
- Added GitHub Actions signed-release workflow.
- Added installer hash verification before an update can launch.
- Added release signing setup documentation.
- Preserved v2.4 recovery, database, installer and CI hardening.
- Stable release feed is `bxane-dev/hadron`.

## 2.4.0

- Added recovery snapshots, database connection hardening, and packaged Windows
  installer/executable smoke gates.

## 2.3.0

- Fixed run-local accounting and detector-response reproducibility.
