param(
    [string]$Adb = "adb",
    [string]$Device = "",
    [string]$Package = "com.valvesoftware.android.steam.community",
    [string]$OutDir = "",
    [string]$Python = "python",
    [switch]$SkipParse,
    [switch]$KeepArchive
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if (-not $OutDir) {
    $OutDir = Join-Path $repoRoot "mafiles\adb_export_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
}

function Get-AdbArgs {
    param([string[]]$AdbArgs)

    $allArgs = @()
    if ($Device) {
        $allArgs += @("-s", $Device)
    }
    $allArgs += $AdbArgs
    return $allArgs
}

function Invoke-AdbChecked {
    param([string[]]$AdbArgs)

    $allArgs = Get-AdbArgs -AdbArgs $AdbArgs
    $output = & $Adb @allArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "adb failed: $Adb $($allArgs -join ' ')`n$output"
    }
    return $output
}

function Invoke-AdbUnchecked {
    param([string[]]$AdbArgs)

    $allArgs = Get-AdbArgs -AdbArgs $AdbArgs
    $output = & $Adb @allArgs 2>&1
    return @{
        ExitCode = $LASTEXITCODE
        Output = $output
    }
}

function Save-AdbExecOut {
    param(
        [string[]]$AdbArgs,
        [string]$Destination
    )

    $allArgs = Get-AdbArgs -AdbArgs $AdbArgs
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $Adb
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    foreach ($arg in $allArgs) {
        [void]$psi.ArgumentList.Add($arg)
    }

    $process = [System.Diagnostics.Process]::Start($psi)
    $stream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $process.StandardOutput.BaseStream.CopyTo($stream)
    }
    finally {
        $stream.Dispose()
    }

    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "adb exec-out failed: $Adb $($allArgs -join ' ')`n$stderr"
    }
}

function Test-AdbAccess {
    param([string[]]$AdbArgs)

    $result = Invoke-AdbUnchecked -AdbArgs $AdbArgs
    return $result.ExitCode -eq 0
}

Write-Host "Checking adb..."
& $Adb version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "adb was not found. Install Android platform-tools or pass -Adb path\to\adb.exe"
}

$devicesOutput = & $Adb devices
$connectedDevices = @(
    $devicesOutput |
        Select-Object -Skip 1 |
        Where-Object { $_ -match "^\S+\s+device$" } |
        ForEach-Object { ($_ -split "\s+")[0] }
)

if (-not $Device) {
    if ($connectedDevices.Count -eq 0) {
        throw "No authorized adb device found. Enable USB debugging and accept the RSA prompt on the phone."
    }
    if ($connectedDevices.Count -gt 1) {
        throw "More than one adb device found. Re-run with -Device <serial>."
    }
    $Device = $connectedDevices[0]
}

Write-Host "Using device: $Device"
Write-Host "Checking package: $Package"
Invoke-AdbChecked -AdbArgs @("shell", "pm", "path", $Package) | Out-Null

$resolvedOutDir = (New-Item -ItemType Directory -Force -Path $OutDir).FullName
$rawDir = Join-Path $resolvedOutDir "raw"
$archivePath = Join-Path $resolvedOutDir "steam_app_data.tgz"
New-Item -ItemType Directory -Force -Path $rawDir | Out-Null

$remoteArchive = "/sdcard/Download/steam_guard_export_$(Get-Date -Format 'yyyyMMdd_HHmmss').tgz"
$appDataPath = "/data/data/$Package"

Write-Host "Checking private app-data access..."
$hasSu = Test-AdbAccess -AdbArgs @("shell", "su", "-c", "id")
$hasRunAs = $false
if (-not $hasSu) {
    $hasRunAs = Test-AdbAccess -AdbArgs @("shell", "run-as", $Package, "id")
}

if ($hasSu) {
    Write-Host "Root access found via su. Creating archive on device..."
    Invoke-AdbChecked -AdbArgs @("shell", "su", "-c", "tar -czf $remoteArchive -C $appDataPath .") | Out-Null
    Invoke-AdbChecked -AdbArgs @("pull", $remoteArchive, $archivePath) | Out-Null
    Invoke-AdbUnchecked -AdbArgs @("shell", "su", "-c", "rm -f $remoteArchive") | Out-Null
}
elseif ($hasRunAs) {
    Write-Host "run-as access found. Creating archive inside app cache..."
    $runAsArchive = "$appDataPath/cache/steam_guard_export.tgz"
    Invoke-AdbChecked -AdbArgs @(
        "shell",
        "run-as",
        $Package,
        "sh",
        "-c",
        "mkdir -p cache && tar -czf cache/steam_guard_export.tgz -C $appDataPath ."
    ) | Out-Null
    Save-AdbExecOut -AdbArgs @("exec-out", "run-as", $Package, "cat", $runAsArchive) -Destination $archivePath
    Invoke-AdbUnchecked -AdbArgs @("shell", "run-as", $Package, "rm", "-f", $runAsArchive) | Out-Null
}
else {
    throw @"
Plain adb cannot read Steam private app data on this device.

This script does not bypass Android sandboxing. You need one of:
- a rooted device where `adb shell su -c id` works;
- a debuggable app build where `adb shell run-as $Package id` works.

After that, re-run this script.
"@
}

Write-Host "Archive saved: $archivePath"
Write-Host "Extracting archive..."
tar -xzf $archivePath -C $rawDir
if ($LASTEXITCODE -ne 0) {
    throw "Could not extract archive with tar. Archive left at: $archivePath"
}

if (-not $KeepArchive) {
    Remove-Item -LiteralPath $archivePath -Force
}

if ($SkipParse) {
    Write-Host "Raw app data extracted: $rawDir"
    exit 0
}

$parserPath = Join-Path $scriptDir "steam_guard_from_adb_export.py"
$mafilePath = Join-Path $resolvedOutDir "steam_guard.maFile"

Write-Host "Scanning export for Steam Guard secrets..."
& $Python $parserPath $rawDir --out $mafilePath --show-sources
if ($LASTEXITCODE -ne 0) {
    throw "Parser did not produce a maFile. Raw export is here: $rawDir"
}

Write-Host ""
Write-Host "Done."
Write-Host "maFile: $mafilePath"
Write-Host "Use it with:"
Write-Host "python debug_buy_listing.py --session ./cookies.pkl --mafile `"$mafilePath`" --auto-confirm --confirm-only <confirmation_id>"
