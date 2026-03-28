# AFKBOT uninstaller for Windows
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

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

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
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
    Refresh-ProcessPath
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "AFKBOT"
}

if (-not $Yes) {
    $answer = Read-Host "Remove AFKBOT install at $InstallDir? [y/N]"
    if ($answer -notin @("y", "Y", "yes", "YES")) {
        throw "Uninstall cancelled."
    }
}

$ShimPath = Join-Path $InstallDir "bin\afk.cmd"
$BinDir = Join-Path $InstallDir "bin"

if (Test-Path $ShimPath) {
    Invoke-Action -Description "$ShimPath uninstall --yes" -Script {
        try {
            & $ShimPath "uninstall" "--yes"
        } catch {
            Write-Warning "Managed uninstall helper failed; continuing with filesystem cleanup."
        }
    }
}

Remove-UserPathEntry -Entry $BinDir

if (Test-Path $InstallDir) {
    Invoke-Action -Description "remove $InstallDir" -Script {
        Remove-Item -Recurse -Force $InstallDir
    }
}

Write-Host "AFKBOT uninstall complete."
