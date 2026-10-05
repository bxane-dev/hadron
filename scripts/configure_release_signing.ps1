param(
    [Parameter(Mandatory=$true)]
    [string]$PrivateKey
)

$ErrorActionPreference = "Stop"
$Repo = "bxane-dev/hadron"
$Secret = "HADRON_RELEASE_SIGNING_KEY_B64"

if (-not (Test-Path $PrivateKey)) {
    throw "Private key not found: $PrivateKey"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

$bytes = [System.IO.File]::ReadAllBytes(
    (Resolve-Path $PrivateKey)
)
$encoded = [Convert]::ToBase64String($bytes)

$encoded | gh secret set $Secret --repo $Repo
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure GitHub Actions signing secret."
}

Write-Host "Configured $Secret for $Repo."
Write-Host "Do not commit or upload the private PEM file."
