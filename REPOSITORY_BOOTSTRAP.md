# Repository Bootstrap

Target:

```text
https://github.com/bxane-dev/hadron
```

This source tree is initialized as a local Git repository with:

- `main`
- a stable source commit authored as `bxane`
- annotated tag `v3.0.0`
- release workflow under `.github/workflows`
- issue/PR templates
- CODEOWNERS for `@bxane-dev`
- Dependabot configuration
- security and contribution guidance

A portable Git bundle is provided separately as:

```text
Hadron-v3.0.0-git.bundle
```

Once the GitHub repository exists:

```powershell
git remote add origin https://github.com/bxane-dev/hadron.git
git push -u origin main
git push origin v3.0.0
```

Or use:

```powershell
.\scripts\publish_github_release.ps1 `
  -CreateRepo `
  -Public `
  -PrivateKey .\Hadron-v3.0.0-bxane-release-private.pem
```

The private signing key is not contained in the Git repository or Git bundle.
