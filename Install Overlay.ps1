param([string]$GamePath, [switch]$Enable, [string]$ConfigPath)
$ErrorActionPreference = 'Stop'

function Get-ApexSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

function Get-ApexIniValue([string]$Text, [string]$Section, [string]$Key) {
    $sectionPattern = '(?ims)(^\[' + [regex]::Escape($Section) + '\][^\S\r\n]*(?:\r?\n|$))(.*?)(?=^\[|\z)'
    $sectionMatch = [regex]::Match($Text, $sectionPattern)
    if (-not $sectionMatch.Success) { return '' }
    $keyMatch = [regex]::Match($sectionMatch.Groups[2].Value, '(?im)^[^\S\r\n]*' + [regex]::Escape($Key) + '[^\S\r\n]*=([^\r\n]*)')
    if ($keyMatch.Success) { return $keyMatch.Groups[1].Value.Trim() }
    return ''
}

function Set-ApexIniValue([string]$Text, [string]$Section, [string]$Key, [string]$Value) {
    $lineBreak = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $sectionRegex = [regex]::new('(?ims)(^\[' + [regex]::Escape($Section) + '\][^\S\r\n]*(?:\r?\n|$))(.*?)(?=^\[|\z)')
    if ($sectionRegex.IsMatch($Text)) {
        return $sectionRegex.Replace($Text, {
            param($sectionMatch)
            $body = $sectionMatch.Groups[2].Value
            $keyRegex = [regex]::new('(?im)^([^\S\r\n]*' + [regex]::Escape($Key) + '[^\S\r\n]*=)[^\r\n]*')
            if ($keyRegex.IsMatch($body)) {
                $body = $keyRegex.Replace($body, { param($keyMatch) $keyMatch.Groups[1].Value + $Value }, 1)
            } else {
                if ($body.Length -gt 0 -and -not $body.EndsWith("`n")) { $body += $lineBreak }
                $body += $Key + '=' + $Value + $lineBreak
            }
            $heading = $sectionMatch.Groups[1].Value
            if (-not $heading.EndsWith("`n")) { $heading += $lineBreak }
            $heading + $body
        }, 1)
    }
    if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { $Text += $lineBreak }
    return $Text + '[' + $Section + ']' + $lineBreak + $Key + '=' + $Value + $lineBreak
}

function Save-ApexConfig([string]$Path, [string]$Text) {
    if ((Test-Path -LiteralPath $Path) -and [IO.File]::ReadAllText($Path) -eq $Text) { return }
    if (Test-Path -LiteralPath $Path) {
        $backup = $Path + '.apex-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.bak'
        Copy-Item -LiteralPath $Path -Destination $backup
        Write-Host ('Previous app settings backed up at ' + $backup)
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}
if (-not $GamePath) {
    $taskSteam = (Get-ItemProperty -Path HKCU:\Software\Valve\Steam -Name SteamPath -ErrorAction SilentlyContinue).SteamPath
    $taskLibraries = @()
    if ($taskSteam) {
        $taskLibraries += $taskSteam
        $taskLibraryFile = Join-Path $taskSteam 'steamapps\libraryfolders.vdf'
        if (Test-Path -LiteralPath $taskLibraryFile) {
            $taskLibraryText = Get-Content -LiteralPath $taskLibraryFile -Raw
            foreach ($taskMatch in [regex]::Matches($taskLibraryText, '"path"\s+"([^"]+)"')) {
                $taskLibraries += $taskMatch.Groups[1].Value.Replace('\\','\')
            }
        }
    }
    foreach ($taskLibrary in $taskLibraries | Select-Object -Unique) {
        $taskCandidate = Join-Path $taskLibrary 'steamapps\common\assettocorsa'
        if (Test-Path -LiteralPath (Join-Path $taskCandidate 'acs.exe')) { $GamePath = $taskCandidate; break }
    }
}
if (-not $GamePath -or -not (Test-Path -LiteralPath (Join-Path $GamePath 'acs.exe'))) { throw 'Assetto Corsa was not found. Run this script with -GamePath and your Assetto Corsa installation folder.' }
$taskSource = Join-Path $PSScriptRoot 'ac_app\RaceEngineer'
foreach ($taskRequired in @('RaceEngineer.py','README.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskSource $taskRequired))) { throw ('The package is missing ' + $taskRequired + '. Extract the complete package and try again.') }
}
$taskBridgeSource = Join-Path $PSScriptRoot 'csp_app\ApexBridge'
foreach ($taskRequired in @('manifest.ini','ApexBridge.lua')) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskBridgeSource $taskRequired))) { throw ('The package is missing its CSP session bridge: ' + $taskRequired + '. Extract the complete package and try again.') }
}
$taskTarget = [IO.Path]::GetFullPath((Join-Path $GamePath 'apps\python\RaceEngineer'))
$taskBridgeTarget = [IO.Path]::GetFullPath((Join-Path $GamePath 'apps\lua\ApexBridge'))
$taskAppRoot = [IO.Path]::GetFullPath((Join-Path $GamePath 'apps\python')) + [IO.Path]::DirectorySeparatorChar
$taskLuaRoot = [IO.Path]::GetFullPath((Join-Path $GamePath 'apps\lua')) + [IO.Path]::DirectorySeparatorChar
if (-not $taskTarget.StartsWith($taskAppRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $taskBridgeTarget.StartsWith($taskLuaRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'An APEX app destination is invalid.' }
foreach ($taskDestination in @($taskTarget, $taskBridgeTarget)) {
    if ((Test-Path -LiteralPath $taskDestination) -and -not (Test-Path -LiteralPath (Join-Path $taskDestination '.apex-race-engineer'))) {
        throw ('An unrelated app already exists at ' + $taskDestination + '. Both apps have been left untouched.')
    }
}
$taskRuntimeSource = Join-Path $taskSource 'stdlib'
if (-not (Test-Path -LiteralPath (Join-Path $taskRuntimeSource 'PROVENANCE.json'))) { throw 'The package is missing its AC Python runtime support.' }
$taskRuntimeManifest = Get-Content -LiteralPath (Join-Path $taskRuntimeSource 'PROVENANCE.json') -Raw | ConvertFrom-Json
foreach ($taskRuntimeFile in $taskRuntimeManifest.files) {
    $taskRuntimePath = Join-Path $taskRuntimeSource $taskRuntimeFile.path
    if ((Get-ApexSha256 $taskRuntimePath) -ne $taskRuntimeFile.sha256) { throw 'AC Python runtime support checksum mismatch.' }
}
# Validation is complete before either destination is changed. No existing
# app files are deleted, including optional user files in an APEX app folder.
foreach ($taskDestination in @($taskTarget, $taskBridgeTarget)) {
    New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $taskDestination '.apex-race-engineer'), 'APEX local race engineer 0.5')
}
foreach ($taskRequired in @('RaceEngineer.py','README.txt')) {
    Copy-Item -LiteralPath (Join-Path $taskSource $taskRequired) -Destination (Join-Path $taskTarget $taskRequired) -Force
}
foreach ($taskRequired in @('manifest.ini','ApexBridge.lua')) {
    Copy-Item -LiteralPath (Join-Path $taskBridgeSource $taskRequired) -Destination (Join-Path $taskBridgeTarget $taskRequired) -Force
}
$taskRuntimeFull = [IO.Path]::GetFullPath($taskRuntimeSource)
foreach ($taskSupportFile in (Get-ChildItem -LiteralPath $taskRuntimeFull -File -Recurse)) {
    $taskRelative = $taskSupportFile.FullName.Substring($taskRuntimeFull.Length).TrimStart('\', '/')
    $taskSupportTarget = Join-Path (Join-Path $taskTarget 'stdlib') $taskRelative
    if ((Test-Path -LiteralPath $taskSupportTarget) -and
        ((Get-ApexSha256 $taskSupportTarget) -eq
         (Get-ApexSha256 $taskSupportFile.FullName))) { continue }
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskSupportTarget) -Force | Out-Null
    Copy-Item -LiteralPath $taskSupportFile.FullName -Destination $taskSupportTarget -Force
}
Write-Host ('APEX overlay installed in ' + $taskTarget)
Write-Host ('APEX CSP session bridge installed in ' + $taskBridgeTarget)
if ($Enable) {
    if (Get-Process -Name acs,acs_x86 -ErrorAction SilentlyContinue) {
        Write-Host 'Assetto Corsa is running. App activation settings were left unchanged so the game cannot overwrite them on exit.'
        Write-Host 'Enable APEX session bridge in the CSP App Shelf, or end the driving session and run this installer again to activate both apps.'
    } else {
        if (-not $ConfigPath) { $ConfigPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Assetto Corsa\cfg' }
        $taskPythonCfg = Join-Path $ConfigPath 'python.ini'
        $taskPythonText = if (Test-Path -LiteralPath $taskPythonCfg) { [IO.File]::ReadAllText($taskPythonCfg) } else { '' }
        Save-ApexConfig $taskPythonCfg (Set-ApexIniValue $taskPythonText 'RACEENGINEER' 'ACTIVE' '1')
        $taskLuaCfg = Join-Path $ConfigPath 'extension\state\imgui_settings.ini'
        $taskLuaText = if (Test-Path -LiteralPath $taskLuaCfg) { [IO.File]::ReadAllText($taskLuaCfg) } else { '' }
        $taskActive = Get-ApexIniValue $taskLuaText 'LUA_APPS' 'ACTIVE'
        if (($taskActive -split ',' | ForEach-Object { $_.Trim() }) -notcontains 'ApexBridge') {
            $taskActive = if ($taskActive.Length -gt 0) { $taskActive + ',ApexBridge' } else { 'ApexBridge' }
        }
        Save-ApexConfig $taskLuaCfg (Set-ApexIniValue $taskLuaText 'LUA_APPS' 'ACTIVE' $taskActive)
        Write-Host 'Enabled the APEX overlay and CSP session bridge. Enter a new driving session to load them.'
    }
}
Write-Host 'In CM: Settings > Assetto Corsa > Apps > enable RaceEngineer. In CSP App Shelf, enable APEX session bridge. Open RaceEngineer from the in-game app sidebar.'
