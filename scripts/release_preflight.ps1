param(
    [switch]$Remote
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

$Args = @(
    "$RepoRoot\hadron_release_preflight.py",
    "--repo-root",
    "$RepoRoot"
)

if ($Remote) {
    $Args += "--remote"
}

python @Args
if ($LASTEXITCODE -ne 0) {
    throw "Hadron v3 release preflight failed."
}
