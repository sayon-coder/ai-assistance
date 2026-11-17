$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Project ".venv\Scripts\pythonw.exe"
$Main = Join-Path $Project "main.py"
if (!(Test-Path $Python)) { throw "TALCON virtual environment not found. Run setup.ps1 first." }
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "TALCON.lnk"
$LegacyPath = Join-Path $Startup "TALCON.lnk"
$Wsh = New-Object -ComObject WScript.Shell
$Shortcut = $Wsh.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = "`"$Main`" --startup"
$Shortcut.WorkingDirectory = $Project
$Shortcut.Description = "TALCON Local Desktop Assistant"
$Shortcut.Save()
if (Test-Path $LegacyPath) { Remove-Item $LegacyPath -Force }
Write-Host "TALCON will start quietly with Windows. Face overlay and tray will appear; the control center stays closed."
