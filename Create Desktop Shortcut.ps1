param()
$ErrorActionPreference = 'Stop'
$taskApplication = Join-Path $PSScriptRoot 'Apex.exe'
if (-not (Test-Path -LiteralPath $taskApplication -PathType Leaf)) {
    throw 'Apex.exe is missing. Extract the complete APEX application package before creating its shortcut.'
}
$taskDesktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $taskDesktop -or -not (Test-Path -LiteralPath $taskDesktop -PathType Container)) {
    throw 'Windows could not locate your Desktop folder.'
}
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut((Join-Path $taskDesktop 'APEX Race Engineer.lnk'))
$taskShortcut.TargetPath = $taskApplication
$taskShortcut.WorkingDirectory = $PSScriptRoot
$taskShortcut.Description = 'APEX - Assetto Corsa race engineer'
$taskShortcut.IconLocation = $taskApplication + ',0'
$taskShortcut.Save()
Write-Host 'Created the APEX Race Engineer desktop shortcut.'
