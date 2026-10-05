# Hadron v3.0 Stable Windows Build

Publisher: **bxane (bxane-dev)**

Run:

```powershell
.\build_windows.bat
```

The stable build targets 14 executables, including `HadronUpdater.exe`.

## Signed GitHub release

Before pushing a `v*` tag, configure the repository signing secret:

```powershell
.\scripts\configure_release_signing.ps1 `
  -PrivateKey .\Hadron-v3.0.0-bxane-release-private.pem
```

The GitHub Actions workflow requires `HADRON_RELEASE_SIGNING_KEY_B64` for tagged
releases.

A tagged release publishes:

```text
Hadron-Setup-v3.0.0.exe
Hadron-v3.0.0-manifest.json
Hadron-v3.0.0-manifest.signature.json
release-public.pem
```

plus the standalone executables.

`HadronUpdater.exe` uses those exact GitHub Release assets.

The private signing key must never be committed.
