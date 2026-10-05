$ErrorActionPreference = "Stop"

Write-Host "=== Hadron Windows Build ==="

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found in PATH. Install Python 3.11+ first."
}

Write-Host "[1/6] Creating virtual environment..."
if (Test-Path ".venv") {
    Remove-Item ".venv" -Recurse -Force
}
python -m venv .venv

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Pip = Join-Path $Root ".venv\Scripts\pip.exe"
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"

Write-Host "[2/6] Installing build dependencies..."
& $Python -m pip install --upgrade pip
& $Pip install -r requirements-build.txt

Write-Host "[3/6] Running automated tests..."
& $Python -m unittest discover -s tests -v

Write-Host "[4/6] Cleaning previous build output..."
Remove-Item "build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "dist" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[5/6] Building Hadron.exe, HadronBatch.exe, HadronJobs.exe, HadronDoctor.exe, HadronVerify.exe, HadronSign.exe, HadronCapsule.exe, HadronReproduce.exe, HadronRegression.exe, HadronCampaign.exe, HadronCampaignRun.exe, and HadronPipeline.exe, HadronRecovery.exe, and HadronUpdater.exe..."
& $PyInstaller --noconfirm --clean "Hadron.spec"
& $PyInstaller --noconfirm --clean "HadronBatch.spec"
& $PyInstaller --noconfirm --clean "HadronJobs.spec"
& $PyInstaller --noconfirm --clean "HadronDoctor.spec"
& $PyInstaller --noconfirm --clean "HadronVerify.spec"
& $PyInstaller --noconfirm --clean "HadronSign.spec"
& $PyInstaller --noconfirm --clean "HadronCapsule.spec"
& $PyInstaller --noconfirm --clean "HadronReproduce.spec"
& $PyInstaller --noconfirm --clean "HadronRegression.spec"
& $PyInstaller --noconfirm --clean "HadronCampaign.spec"
& $PyInstaller --noconfirm --clean "HadronCampaignRun.spec"
& $PyInstaller --noconfirm --clean "HadronPipeline.spec"
& $PyInstaller --noconfirm --clean "HadronRecovery.spec"
& $PyInstaller --noconfirm --clean "HadronUpdater.spec"

if (-not (Test-Path "dist\Hadron.exe")) {
    throw "PyInstaller did not create dist\Hadron.exe"
}
if (-not (Test-Path "dist\HadronBatch.exe")) {
    throw "PyInstaller did not create dist\HadronBatch.exe"
}
if (-not (Test-Path "dist\HadronJobs.exe")) {
    throw "PyInstaller did not create dist\HadronJobs.exe"
}
if (-not (Test-Path "dist\HadronDoctor.exe")) {
    throw "PyInstaller did not create dist\HadronDoctor.exe"
}
if (-not (Test-Path "dist\HadronVerify.exe")) {
    throw "PyInstaller did not create dist\HadronVerify.exe"
}
if (-not (Test-Path "dist\HadronSign.exe")) {
    throw "PyInstaller did not create dist\HadronSign.exe"
}
if (-not (Test-Path "dist\HadronCapsule.exe")) {
    throw "PyInstaller did not create dist\HadronCapsule.exe"
}
if (-not (Test-Path "dist\HadronReproduce.exe")) {
    throw "PyInstaller did not create dist\HadronReproduce.exe"
}
if (-not (Test-Path "dist\HadronRegression.exe")) {
    throw "PyInstaller did not create dist\HadronRegression.exe"
}
if (-not (Test-Path "dist\HadronCampaign.exe")) {
    throw "PyInstaller did not create dist\HadronCampaign.exe"
}
if (-not (Test-Path "dist\HadronCampaignRun.exe")) {
    throw "PyInstaller did not create dist\HadronCampaignRun.exe"
}
if (-not (Test-Path "dist\HadronPipeline.exe")) {
    throw "PyInstaller did not create dist\HadronPipeline.exe"
}
if (-not (Test-Path "dist\HadronRecovery.exe")) {
    throw "PyInstaller did not create dist\HadronRecovery.exe"
}
if (-not (Test-Path "dist\HadronUpdater.exe")) {
    throw "PyInstaller did not create dist\HadronUpdater.exe"
}

Write-Host "[6/6] Building installer and release manifest..."
$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)

$Iscc = $null
foreach ($Candidate in $IsccCandidates) {
    if (Test-Path $Candidate) {
        $Iscc = $Candidate
        break
    }
}

if ($Iscc) {
    & $Iscc "installer\Hadron.iss"
    Write-Host ""
    Write-Host "Installer created in release\"
} else {
    Write-Warning "Inno Setup 6 was not found."
    Write-Warning "Hadron.exe was still built successfully at dist\Hadron.exe."
    Write-Warning "Install Inno Setup 6 and run installer\Hadron.iss to create the installer."
}

Write-Host "Running packaged CLI smoke tests..."
$SmokeDir = Join-Path $env:TEMP "hadron-v3.0-smoke"
Remove-Item $SmokeDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $SmokeDir | Out-Null

& ".\dist\HadronDoctor.exe" --data-dir $SmokeDir --benchmark-events 100 --output (Join-Path $SmokeDir "doctor.json")
if ($LASTEXITCODE -ne 0) { throw "HadronDoctor.exe smoke test failed." }

& ".\dist\HadronBatch.exe" --events 100 --seed 2400 --output (Join-Path $SmokeDir "batch.json")
if ($LASTEXITCODE -ne 0) { throw "HadronBatch.exe smoke test failed." }

& ".\dist\HadronJobs.exe" "example_jobs.json" --workers 2 --output (Join-Path $SmokeDir "jobs.json")
if ($LASTEXITCODE -ne 0) { throw "HadronJobs.exe multiworker smoke test failed." }

& ".\dist\HadronRecovery.exe" create --data-dir $SmokeDir --output (Join-Path $SmokeDir "recovery.hadron-recovery.zip")
if ($LASTEXITCODE -ne 0) { throw "HadronRecovery.exe create smoke test failed." }

& ".\dist\HadronRecovery.exe" inspect (Join-Path $SmokeDir "recovery.hadron-recovery.zip")
if ($LASTEXITCODE -ne 0) { throw "HadronRecovery.exe inspect smoke test failed." }

& ".\dist\HadronUpdater.exe" --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "HadronUpdater.exe help smoke test failed." }

$ManifestFiles = @(
    "dist\Hadron.exe",
    "dist\HadronBatch.exe",
    "dist\HadronJobs.exe",
    "dist\HadronDoctor.exe",
    "dist\HadronVerify.exe",
    "dist\HadronSign.exe",
    "dist\HadronCapsule.exe",
    "dist\HadronReproduce.exe",
    "dist\HadronRegression.exe",
    "dist\HadronCampaign.exe",
    "dist\HadronCampaignRun.exe",
    "dist\HadronPipeline.exe",
    "dist\HadronRecovery.exe",
    "dist\HadronUpdater.exe"
)
if (Test-Path "release\Hadron-Setup-v3.0.0.exe") {
    $ManifestFiles += "release\Hadron-Setup-v3.0.0.exe"
}
& $Python "scripts\make_release_manifest.py" --output "release\Hadron-v3.0.0-manifest.json" @ManifestFiles

Write-Host ""
Write-Host "Build complete."
