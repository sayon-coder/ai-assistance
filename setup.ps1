param([switch]$SkipStartupPrompt)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Prefer a 64-bit Python 3.11+; fall back to whatever "py"/"python" provides.
$Version = $null
foreach ($Candidate in @("3.14", "3.13", "3.12", "3.11")) {
    & py -$Candidate --version *> $null
    if ($LASTEXITCODE -eq 0) { $Version = $Candidate; break }
}
if (!$Version) {
    $Py = Get-Command py -ErrorAction SilentlyContinue
    if (!$Py) {
        $Py = Get-Command python -ErrorAction SilentlyContinue
        if (!$Py) { throw "Python 3.11 or newer (64-bit) is required. Install it from python.org first." }
        & python --version *> $null
        if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required. Install it from python.org first." }
        if (!(Test-Path ".venv")) { & python -m venv .venv }
    } else {
        if (!(Test-Path ".venv")) { & py -m venv .venv }
    }
} else {
    if (!(Test-Path ".venv")) { & py -$Version -m venv .venv }
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed (pip exit $LASTEXITCODE)." }

Write-Host "Dependencies installed."
Write-Host "The offline speech model is downloaded automatically by 'Install NOVA.bat',"
Write-Host "or later from NOVA > Voice > Download model (free, Apache 2.0, no account)."

if (!$SkipStartupPrompt) {
    $Install = Read-Host "Install NOVA to start with Windows? (Y/N)"
    if ($Install -match "^(Y|YES)$") { & powershell -ExecutionPolicy Bypass -File ".\scripts\install_startup.ps1" }
    Write-Host "Run NOVA with: Launch NOVA.bat"
}
