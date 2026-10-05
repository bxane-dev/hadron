# Publishing Hadron v3.0.0

Target repository:

```text
https://github.com/bxane-dev/hadron
```

The connected ChatGPT GitHub integration currently cannot see that repository,
so the final push cannot be performed from this chat until the repository exists
and is granted to the GitHub connection.

## One-command publisher

From the extracted Hadron v3.0 source directory:

```powershell
.\scripts\publish_github_release.ps1 `
  -CreateRepo `
  -Public `
  -PrivateKey .\Hadron-v3.0.0-bxane-release-private.pem
```

This performs:

1. GitHub CLI authentication check
2. repository existence check
3. optional creation of `bxane-dev/hadron`
4. local Git initialization if required
5. origin configuration
6. release-signing secret upload
7. stable source commit
8. push to `main`
9. creation of annotated `v3.0.0` tag
10. tag push that starts the GitHub Actions release workflow

The workflow then builds, tests, signs, verifies, installs, smoke-tests, and
publishes the Windows release.

## Security

The private release key must stay outside the Git repository.

The repository `.gitignore` blocks release private PEM files. The public key is
safe to commit and is pinned by `HadronUpdater.exe`.


## Preflight

Before pushing:

```powershell
.\scripts\release_preflight.ps1
```

Once the GitHub repository exists publicly:

```powershell
.\scripts\release_preflight.ps1 -Remote
```

The preflight checks:

- stable version/publisher/repository identity
- required release/update files
- absence of private PEM keys from the repository
- clean Git working tree
- `v3.0.0` points to `HEAD`
- signing secret requirement in the workflow
- updater/signature assets in the workflow
- pinned updater key fingerprint
- optional remote GitHub repository existence


## Repository configuration

After `main` exists on GitHub:

```powershell
.\scripts\configure_github_repository.ps1 `
  -Repo bxane-dev/hadron `
  -Public
```

This configures:

- repository description/homepage
- issues and discussions
- project topics
- GitHub Actions write permission for release assets
- basic protection against force-push/deletion of `main` when supported

The main publishing script now runs this automatically after the first push.
