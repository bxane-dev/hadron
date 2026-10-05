# Security Policy

## Supported release

Hadron 3.x is the current stable line.

## Sensitive material

Never commit:

- Hadron Ed25519 release private keys
- GitHub tokens
- signing secrets
- user databases
- recovery snapshots containing user data
- credentials from connected services

The public release key is intentionally distributed with Hadron and is not
secret.

## Release integrity

Official updater-compatible releases are expected to contain:

- a Windows installer
- a Hadron release manifest
- an Ed25519 manifest signature
- the public verification key

`HadronUpdater.exe` validates publisher identity, repository identity, the
pinned Ed25519 signature, installer size, and installer SHA-256 before launch.

## Reporting

Use GitHub's private security-advisory feature for `bxane-dev/hadron` when the
repository is available. Avoid posting exploitable security details in a public
issue before a fix exists.
