param()
$ErrorActionPreference = 'Stop'
$taskApplication = Join-Path $PSScriptRoot 'Apex.exe'
if (-not (Test-Path -LiteralPath $taskApplication -PathType Leaf)) {
    throw 'Apex.exe is missing. Extract the complete APEX application package and run this launcher from that folder.'
}

# APEX is framework-dependent. Check installed local runtime folders only;
# this launcher never installs a runtime or opens a download page.
$taskRuntimeRoots = @()
if ($env:DOTNET_ROOT) { $taskRuntimeRoots += $env:DOTNET_ROOT }
if ($env:ProgramFiles) { $taskRuntimeRoots += (Join-Path $env:ProgramFiles 'dotnet') }
$taskDotnet = Get-Command dotnet.exe -ErrorAction SilentlyContinue
if ($taskDotnet) { $taskRuntimeRoots += (Split-Path -Parent $taskDotnet.Source) }
$taskHasRuntime = $false
foreach ($taskRuntimeRoot in ($taskRuntimeRoots | Select-Object -Unique)) {
    $taskDesktopRuntime = Join-Path $taskRuntimeRoot 'shared\Microsoft.WindowsDesktop.App'
    if (-not (Test-Path -LiteralPath $taskDesktopRuntime -PathType Container)) { continue }
    foreach ($taskVersionFolder in (Get-ChildItem -LiteralPath $taskDesktopRuntime -Directory)) {
        $taskRuntimeVersion = $null
        if ([version]::TryParse($taskVersionFolder.Name, [ref]$taskRuntimeVersion) -and $taskRuntimeVersion.Major -eq 10) {
            $taskHasRuntime = $true
            break
        }
    }
    if ($taskHasRuntime) { break }
}
if (-not $taskHasRuntime) {
    throw 'APEX needs the x64 .NET 10 Windows Desktop Runtime. Install that runtime, then reopen Apex.exe.'
}

# The visible program is the native Windows application. It manages its own
# local companion and process ownership; no browser is involved.
Start-Process -FilePath $taskApplication -WorkingDirectory $PSScriptRoot -WindowStyle Normal
