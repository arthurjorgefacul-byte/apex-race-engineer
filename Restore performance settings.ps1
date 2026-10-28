param([string]$GamePath)
$ErrorActionPreference = 'Stop'
if (-not $GamePath) {
    $taskSteam = (Get-ItemProperty -Path HKCU:\Software\Valve\Steam -Name SteamPath -ErrorAction SilentlyContinue).SteamPath
    if ($taskSteam) { $GamePath = Join-Path $taskSteam 'steamapps\common\assettocorsa' }
}
if (-not $GamePath -or -not (Test-Path -LiteralPath (Join-Path $GamePath 'acs.exe'))) {
    throw 'Specify -GamePath with the Assetto Corsa installation folder.'
}
function Restore-ApexSetting([string]$Path, [string]$Backup, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Backup)) { Write-Host ('No APEX backup for ' + $Key + '; left unchanged.'); return }
    $taskOld = [IO.File]::ReadAllText($Backup)
    $taskMatch = [regex]::Match($taskOld, '(?m)^' + [regex]::Escape($Key) + '=([0-9.]+)\s*$')
    if (-not $taskMatch.Success) { throw ('The original ' + $Key + ' value could not be read.') }
    $taskValue = $taskMatch.Groups[1].Value
    $taskText = [IO.File]::ReadAllText($Path)
    $taskPattern = '(?m)^' + [regex]::Escape($Key) + '=.*$'
    $taskReplacement = $Key + '=' + $taskValue
    $taskText = [regex]::Replace($taskText, $taskPattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($taskM) $taskReplacement })
    [IO.File]::WriteAllText($Path, $taskText, (New-Object Text.UTF8Encoding($false)))
    Write-Host ('Restored ' + $taskReplacement)
}
$taskVideo = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Assetto Corsa\cfg\video.ini'
$taskConfig = Join-Path $GamePath 'system\cfg\assetto_corsa.ini'
Restore-ApexSetting $taskVideo ($taskVideo + '.apex-before-fps-cap-20261027.bak') 'FPS_CAP_MS'
Restore-ApexSetting $taskConfig ($taskConfig + '.apex-before-full-session-export-20261027.bak') 'EXPORT_ENTIRE_SESSION'
Write-Host 'Restart AC for the restored settings to apply. APEX recordings were left intact.'
