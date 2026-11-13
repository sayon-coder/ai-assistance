$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "NOVA.lnk"
if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force; Write-Host "NOVA removed from Windows startup." }
else { Write-Host "NOVA startup shortcut was not found." }
