# AFKBOT installer for Windows
[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/afkbot-io/afkbotio.git",
    [string]$GitRef = "main",
    [switch]$SkipSetup,
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

function Get-UserBinDir {
    if (-not [string]::IsNullOrWhiteSpace($env:XDG_BIN_HOME)) {
        return $env:XDG_BIN_HOME
    }
    return Join-Path $env:USERPROFILE ".local\bin"
}

function Get-UvExePath {
    return Join-Path (Get-UserBinDir) "uv.exe"
}

function Resolve-LocalSourcePath([string]$Value) {
    if ($Value.StartsWith("file://")) {
        return $Value.Substring(7)
    }
    if (Test-Path $Value) {
        return (Resolve-Path $Value).Path
    }
    return $null
}

function Get-ToolSource {
    $localSource = Resolve-LocalSourcePath $RepoUrl
    if ($null -ne $localSource) {
        return @{
            Mode = "editable"
            Value = $localSource
        }
    }

    $normalized = $RepoUrl.Trim()
    if ($normalized.StartsWith("git@github.com:")) {
        $normalized = "https://github.com/" + $normalized.Substring("git@github.com:".Length)
    }
    if ($normalized.EndsWith(".git")) {
        $normalized = $normalized.Substring(0, $normalized.Length - 4)
    }
    $normalized = $normalized.TrimEnd("/")
    if (
        $normalized.StartsWith("https://github.com/") -or
        $normalized.StartsWith("http://github.com/") -or
        $normalized.StartsWith("https://www.github.com/") -or
        $normalized.StartsWith("http://www.github.com/")
    ) {
        return @{
            Mode = "git"
            Value = "git+$normalized.git@$GitRef"
        }
    }

    throw "Installer supports a local source path or a GitHub repository URL."
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

function Ensure-Uv([string]$UvExe) {
    if (Test-Path $UvExe) {
        return
    }

    $uvDir = Split-Path -Parent $UvExe
    if ($DryRun) {
        Write-Host "[dry-run] install uv into $uvDir"
        return
    }

    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    $installer = Join-Path $env:TEMP ("afkbot-uv-" + [guid]::NewGuid().ToString("N") + ".ps1")
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer
        $env:UV_UNMANAGED_INSTALL = $uvDir
        [void](Invoke-NativeCommand -Description "& $installer" -Script {
            & $installer
        })
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $installer
        Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $UvExe)) {
        throw "uv installation completed but $UvExe was not created."
    }
}

function Get-ToolBinDir([string]$UvExe) {
    if ($DryRun) {
        return Get-UserBinDir
    }
    $output = Invoke-NativeCommand -Description "$UvExe tool dir --bin" -Script {
        & $UvExe tool dir --bin
    }
    if ([string]::IsNullOrWhiteSpace($output)) {
        throw "uv did not report a tool executable directory."
    }
    return $output.Trim()
}

function Install-AfkTool([string]$UvExe, [hashtable]$ToolSource) {
    if ($ToolSource.Mode -eq "editable") {
        [void](Invoke-NativeCommand -Description "$UvExe tool install --python 3.12 --reinstall --editable $($ToolSource.Value)" -Script {
            & $UvExe tool install --python 3.12 --reinstall --editable $ToolSource.Value
        })
        return
    }

    [void](Invoke-NativeCommand -Description "$UvExe tool install --python 3.12 --reinstall $($ToolSource.Value)" -Script {
        & $UvExe tool install --python 3.12 --reinstall $ToolSource.Value
    })
}

function Update-ToolShell([string]$UvExe) {
    if ($DryRun) {
        Write-Host "[dry-run] $UvExe tool update-shell"
        return
    }
    try {
        [void](Invoke-NativeCommand -Description "$UvExe tool update-shell" -Script {
            & $UvExe tool update-shell
        })
    } catch {
        Write-Warning "uv tool update-shell failed; reopen the shell if 'afk' is not yet visible."
    }
}

$uvExe = Get-UvExePath
Ensure-Uv -UvExe $uvExe
$toolSource = Get-ToolSource
Install-AfkTool -UvExe $uvExe -ToolSource $toolSource
Update-ToolShell -UvExe $uvExe

$toolBinDir = Get-ToolBinDir -UvExe $uvExe
$afkCmd = Join-Path $toolBinDir "afk.cmd"
$env:Path = "$toolBinDir;$env:Path"

if (-not $SkipSetup) {
    [void](Invoke-NativeCommand -Description "$afkCmd setup --bootstrap-only --yes" -Script {
        & $afkCmd "setup" "--bootstrap-only" "--yes"
    })
}

$legacyManagedBin = Join-Path $env:LOCALAPPDATA "AFKBOT\bin"
Remove-UserPathEntry -Entry $legacyManagedBin

Write-Host ""
Write-Host "AFKBOT install complete."
Write-Host "Tool source: $($toolSource.Value)"
Write-Host "uv: $uvExe"
Write-Host "CLI: $afkCmd"
Write-Host ""
Write-Host "If 'afk' is not visible in the current shell yet, reopen the terminal."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  afk setup"
Write-Host "  afk doctor"
Write-Host "  afk chat"
Write-Host ""
Write-Host "To update later, run `afk update` or `uv tool upgrade afkbotio --reinstall`."
