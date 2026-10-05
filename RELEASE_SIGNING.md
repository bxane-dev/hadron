# Hadron v3 Release Signing

Release identity:

- Publisher: **bxane**
- GitHub: **bxane-dev**
- Repository: **bxane-dev/hadron**
- Algorithm: **Ed25519**

Pinned public-key fingerprint:

```text
738c934d58220a2420103e891add9c7602b119d00a19ff51e9b69ca854a3ee76
```

`release-public.pem` is committed and embedded into `HadronUpdater.exe`.

The matching private key is intentionally **not** stored in this source tree.

## GitHub Actions secret

The release workflow expects:

```text
HADRON_RELEASE_SIGNING_KEY_B64
```

On Windows with GitHub CLI:

```powershell
.\scripts\configure_release_signing.ps1 `
  -PrivateKey .\Hadron-v3.0.0-bxane-release-private.pem
```

The private key is base64-encoded and stored as a GitHub Actions secret.

## Release verification chain

For a tagged release, GitHub Actions:

1. builds and smoke-tests the Windows binaries
2. builds and smoke-tests the installer
3. generates `Hadron-vX.Y.Z-manifest.json`
4. signs that exact manifest with the bxane Ed25519 key
5. verifies the signature against `release-public.pem`
6. publishes installer + manifest + signature + public key
7. `HadronUpdater.exe` downloads those assets and repeats signature/hash
   verification before launching the installer

This is Hadron's application-level release signature. It is separate from
Microsoft Authenticode code signing; Authenticode requires a Windows code-signing
certificate and is not claimed by this release.
