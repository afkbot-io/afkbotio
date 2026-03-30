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
        throw ("Command failed with exit code {0}: {1}" -f $exitCode, $Description)
    }
    return $result
}

function Get-UvExePath {
    return Join-Path (Get-UserBinDir) "uv.exe"
}

function Get-HomeDir {
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        return $env:USERPROFILE
    }
    if (-not [string]::IsNullOrWhiteSpace($env:HOME)) {
        return $env:HOME
    }
    $profilePath = [Environment]::GetFolderPath("UserProfile")
    if (-not [string]::IsNullOrWhiteSpace($profilePath)) {
        return $profilePath
    }
    throw "Could not determine the user home directory."
}

function Get-UserBinDir {
    if (-not [string]::IsNullOrWhiteSpace($env:XDG_BIN_HOME)) {
        return $env:XDG_BIN_HOME
    }
    return Join-Path (Get-HomeDir) ".local\bin"
}

function Get-LegacyInstallDir {
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return (Join-Path $env:LOCALAPPDATA "AFKBOT")
    }
    $localAppData = [Environment]::GetFolderPath("LocalApplicationData")
    if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
        return (Join-Path $localAppData "AFKBOT")
    }
    return (Join-Path (Get-HomeDir) ".local/share/afkbot")
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

function Get-AfkToolPath([string]$ToolBinDir) {
    $exePath = Join-Path $ToolBinDir "afk.exe"
    if (Test-Path $exePath) {
        return $exePath
    }
    $cmdPath = Join-Path $ToolBinDir "afk.cmd"
    return $cmdPath
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

$legacyInstallDir = Get-LegacyInstallDir
$legacyManagedBin = Join-Path $legacyInstallDir "bin"
$uvExe = Get-UvExePath
$toolBinDir = Get-ToolBinDir -UvExe $uvExe
$afkCmd = Get-AfkToolPath -ToolBinDir $toolBinDir
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
