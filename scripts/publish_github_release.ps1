param(
    [string]$Repo = "bxane-dev/hadron",
    [string]$Tag = "v3.0.0",
    [string]$PrivateKey = "",
    [switch]$CreateRepo,
    [switch]$Public
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found in PATH."
    }
}

Require-Command git
Require-Command gh

Write-Host "Hadron v3 GitHub publisher"
Write-Host "Repository: $Repo"
Write-Host "Tag:        $Tag"

gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

$RepoExists = $true
gh repo view $Repo --json nameWithOwner | Out-Null
if ($LASTEXITCODE -ne 0) {
    $RepoExists = $false
}

if (-not $RepoExists) {
    if (-not $CreateRepo) {
        throw "Repository $Repo does not exist or is not accessible. Re-run with -CreateRepo once your GitHub account has permission to create it."
    }

    $Visibility = if ($Public) { "--public" } else { "--private" }
    Write-Host "Creating repository $Repo..."
    gh repo create $Repo $Visibility --description "Hadron synthetic particle accelerator and reproducible research simulator"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $Repo."
    }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path ".git")) {
    git init
    git branch -M main
}

$Origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    git remote add origin "https://github.com/$Repo.git"
} elseif ($Origin -ne "https://github.com/$Repo.git" -and $Origin -ne "git@github.com:$Repo.git") {
    git remote set-url origin "https://github.com/$Repo.git"
}

if ($PrivateKey) {
    if (-not (Test-Path $PrivateKey)) {
        throw "Private signing key not found: $PrivateKey"
    }
    & "$PSScriptRoot\configure_release_signing.ps1" -PrivateKey $PrivateKey
}

git add .
if ((git status --porcelain).Length -gt 0) {
    git commit -m "Hadron v3.0.0 stable by bxane (bxane-dev)"
}

git push -u origin main

Write-Host "Configuring GitHub repository settings..."
& "$PSScriptRoot\configure_github_repository.ps1" -Repo $Repo -Public:$Public
if ($LASTEXITCODE -ne 0) {
    throw "GitHub repository configuration failed."
}

$ExistingTag = git tag --list $Tag
if (-not $ExistingTag) {
    git tag -a $Tag -m "Hadron v3.0.0 Stable — bxane (bxane-dev)"
}
git push origin $Tag

Write-Host ""
Write-Host "Tag pushed. GitHub Actions will build, test, sign, and publish the release."
Write-Host "Monitor:"
Write-Host "https://github.com/$Repo/actions"
Write-Host "Release:"
Write-Host "https://github.com/$Repo/releases/tag/$Tag"
