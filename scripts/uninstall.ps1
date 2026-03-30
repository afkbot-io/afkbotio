# AFKBOT uninstaller for Windows
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
    Write-Warning "-InstallDir is ignored by the uv tool uninstaller."
}

function Invoke-Action {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Script,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if ($DryRun) {
        Write-Host "[dry-run] $Description"
        return
    }
    & $Script
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Script,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if ($DryRun) {
        Write-Host "[dry-run] $Description"
        return $null
    }

    $global:LASTEXITCODE = 0
    $result = & $Script
    $exitCode = $global:LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Command failed with exit code $exitCode: $Description"
    }
    return $result
}

function Get-UvExePath {
    $userBinDir = if (-not [string]::IsNullOrWhiteSpace($env:XDG_BIN_HOME)) {
        $env:XDG_BIN_HOME
    } else {
        Join-Path $env:USERPROFILE ".local\bin"
    }
    return Join-Path $userBinDir "uv.exe"
}

function Get-ToolBinDir([string]$UvExe) {
    if ($DryRun -or -not (Test-Path $UvExe)) {
        return Split-Path -Parent $UvExe
    }
    $output = Invoke-NativeCommand -Description "$UvExe tool dir --bin" -Script {
        & $UvExe tool dir --bin
    }
    if ([string]::IsNullOrWhiteSpace($output)) {
        throw "uv did not report a tool executable directory."
    }
    return $output.Trim()
}

function Remove-UserPathEntry([string]$Entry) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($current)) {
        return
    }
    $parts = @($current -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and $_ -ine $Entry })
    if ($DryRun) {
        Write-Host "[dry-run] remove $Entry from user PATH"
        return
    }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

if (-not $Yes) {
    $answer = Read-Host "Remove AFKBOT tool install and local runtime state? [y/N]"
    if ($answer -notin @("y", "Y", "yes", "YES")) {
        throw "Uninstall cancelled."
    }
}

$legacyInstallDir = Join-Path $env:LOCALAPPDATA "AFKBOT"
$legacyManagedBin = Join-Path $legacyInstallDir "bin"
$uvExe = Get-UvExePath
$toolBinDir = Get-ToolBinDir -UvExe $uvExe
$afkCmd = Join-Path $toolBinDir "afk.cmd"
$hadWarnings = $false

if (Test-Path $afkCmd) {
    try {
        [void](Invoke-NativeCommand -Description "$afkCmd uninstall --yes" -Script {
            & $afkCmd "uninstall" "--yes"
        })
    } catch {
        $hadWarnings = $true
        Write-Warning "AFKBOT runtime cleanup failed; continuing with tool uninstall."
    }
}

if (Test-Path $uvExe) {
    try {
        [void](Invoke-NativeCommand -Description "$uvExe tool uninstall afkbotio" -Script {
            & $uvExe tool uninstall afkbotio
        })
    } catch {
        $hadWarnings = $true
        Write-Warning "uv tool uninstall failed; continuing with legacy cleanup."
    }
}

Remove-UserPathEntry -Entry $legacyManagedBin

if (Test-Path $legacyInstallDir) {
    Invoke-Action -Description "remove $legacyInstallDir" -Script {
        Remove-Item -Recurse -Force $legacyInstallDir
    }
}

if ($hadWarnings) {
    Write-Host "AFKBOT uninstall complete with warnings."
} else {
    Write-Host "AFKBOT uninstall complete."
}
