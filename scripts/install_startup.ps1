$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Project ".venv\Scripts\pythonw.exe"
$Main = Join-Path $Project "main.py"
if (!(Test-Path $Python)) { throw "NOVA virtual environment not found. Run setup.ps1 first." }
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "NOVA.lnk"
$Wsh = New-Object -ComObject WScript.Shell
$Shortcut = $Wsh.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = "`"$Main`" --startup"
$Shortcut.WorkingDirectory = $Project
$Shortcut.Description = "NOVA Local Desktop Assistant"
$Shortcut.Save()
Write-Host "NOVA will start quietly with Windows. Face overlay and tray will appear; the control center stays closed."
