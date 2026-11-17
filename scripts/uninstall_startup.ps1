$Startup = [Environment]::GetFolderPath("Startup")
$Removed = $false
foreach ($Name in @("TALCON.lnk", "TALCON.lnk")) {
    $ShortcutPath = Join-Path $Startup $Name
    if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force; $Removed = $true }
}
if ($Removed) { Write-Host "TALCON removed from Windows startup." }
else { Write-Host "TALCON startup shortcut was not found." }
