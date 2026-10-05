# Hadron v3.0.0 Stable

**Publisher:** bxane  
**GitHub:** bxane-dev  
**Repository:** `bxane-dev/hadron`

Hadron v3.0 is the stable milestone of the synthetic particle-accelerator and
research-workspace simulator developed through the v0.x, v1.x and v2.x series.

## Stable systems

Hadron v3.0 includes:

- deterministic toy collider engine
- configurable detector noise/resolution
- L1 + HLT toy trigger pipeline
- study queue and study result analysis
- campaigns and executable campaign verification
- reproducibility capsules
- deterministic capsule reruns
- multi-capsule regression gates
- campaign pipelines
- Ed25519 release signatures
- audit chains and provenance
- recovery snapshots
- Windows installer and standalone tools
- signed GitHub Releases auto-updater

## Signed auto-updates

Hadron checks this feed:

```text
https://github.com/bxane-dev/hadron/releases
```

The app automatically checks for a newer stable release after startup.

It does **not** silently install updates. Installation requires the user to
choose **System Tools → Updates → Install Update**.

Before the installer is launched, Hadron requires all of the following:

1. a newer non-prerelease GitHub release
2. `Hadron-Setup-vX.Y.Z.exe`
3. `Hadron-vX.Y.Z-manifest.json`
4. `Hadron-vX.Y.Z-manifest.signature.json`
5. a valid Ed25519 signature from the pinned bxane release key
6. publisher identity `bxane / bxane-dev / bxane-dev/hadron`
7. installer SHA-256 and size matching the signed manifest

Pinned key fingerprint:

```text
738c934d58220a2420103e891add9c7602b119d00a19ff51e9b69ca854a3ee76
```

The updater can also be used directly:

```powershell
HadronUpdater.exe check

HadronUpdater.exe download `
  --dir "$env:LOCALAPPDATA\Hadron\updates"

HadronUpdater.exe apply
```

Set `HADRON_DISABLE_UPDATE_CHECK=1` to disable the automatic startup check.

## Stable format contract

Public artifact formats are frozen at v1 for Hadron 3.0. Breaking changes must
increment their individual format version. See `STABLE_FORMATS.md`.

SQLite stays at schema **v12**, preserving the existing migration chain.

## Release signing

Tagged releases are signed as:

```text
bxane (bxane-dev)
```

See `RELEASE_SIGNING.md`.

The private Ed25519 release key is not committed to this repository. GitHub
Actions reads it from `HADRON_RELEASE_SIGNING_KEY_B64`.

This is application-level release signing, not Microsoft Authenticode.

## Windows release targets

v3.0 builds 14 executable targets:

```text
Hadron.exe
HadronBatch.exe
HadronJobs.exe
HadronDoctor.exe
HadronVerify.exe
HadronSign.exe
HadronCapsule.exe
HadronReproduce.exe
HadronRegression.exe
HadronCampaign.exe
HadronCampaignRun.exe
HadronPipeline.exe
HadronRecovery.exe
HadronUpdater.exe
```

Installer:

```text
Hadron-Setup-v3.0.0.exe
```

## Scope

Hadron is synthetic/toy simulation software. It does not connect to or control a
real accelerator and does not represent real detector measurements.


## Publishing

Use:

```powershell
.\scripts\publish_github_release.ps1 `
  -CreateRepo `
  -Public `
  -PrivateKey .\Hadron-v3.0.0-bxane-release-private.pem
```

See `PUBLISHING.md`.
