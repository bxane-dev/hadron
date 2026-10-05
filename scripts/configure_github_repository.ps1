param(
    [string]$Repo = "bxane-dev/hadron",
    [switch]$Public = $true
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

gh repo view $Repo --json nameWithOwner | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Repository $Repo does not exist or is not accessible."
}

Write-Host "Configuring $Repo..."

$Visibility = if ($Public) { "public" } else { "private" }

gh repo edit $Repo `
  --description "Hadron synthetic particle accelerator and reproducible research simulator" `
  --homepage "https://github.com/$Repo" `
  --visibility $Visibility `
  --enable-issues `
  --enable-discussions

if ($LASTEXITCODE -ne 0) {
    throw "Failed to update repository metadata."
}

$Topics = @(
  "python",
  "simulation",
  "particle-physics",
  "reproducibility",
  "desktop-app",
  "customtkinter",
  "research-tools",
  "windows"
)

foreach ($Topic in $Topics) {
    gh repo edit $Repo --add-topic $Topic
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to add topic: $Topic"
    }
}

# Allow the release workflow to create GitHub Release assets.
gh api `
  --method PUT `
  -H "Accept: application/vnd.github+json" `
  "/repos/$Repo/actions/permissions/workflow" `
  -f default_workflow_permissions=write `
  -F can_approve_pull_request_reviews=false | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure GitHub Actions workflow permissions."
}

# Enable basic branch protection after main exists.
$MainExists = $true
gh api "/repos/$Repo/branches/main" | Out-Null
if ($LASTEXITCODE -ne 0) {
    $MainExists = $false
}

if ($MainExists) {
    $Protection = @{
        required_status_checks = $null
        enforce_admins = $false
        required_pull_request_reviews = $null
        restrictions = $null
        required_linear_history = $true
        allow_force_pushes = $false
        allow_deletions = $false
        block_creations = $false
        required_conversation_resolution = $false
        lock_branch = $false
        allow_fork_syncing = $true
    } | ConvertTo-Json -Depth 6

    $Protection | gh api `
      --method PUT `
      -H "Accept: application/vnd.github+json" `
      -H "Content-Type: application/json" `
      "/repos/$Repo/branches/main/protection" `
      --input - | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Branch protection could not be configured. This can depend on repository visibility/account permissions."
    }
}

Write-Host ""
Write-Host "Repository configuration complete."
Write-Host "Repository: https://github.com/$Repo"
Write-Host "Actions:    https://github.com/$Repo/actions"
Write-Host "Releases:   https://github.com/$Repo/releases"
