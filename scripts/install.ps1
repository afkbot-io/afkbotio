# AFKBOT installer for Windows
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$RepoUrl = "https://github.com/afkbot-io/afkbotio.git",
    [string]$GitRef = "main",
    [switch]$SkipSetup,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$Message) {
    Write-Host $Message
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

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
}

function Add-UserPathEntry([string]$Entry) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        $parts = @($current -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    if ($parts | Where-Object { $_ -ieq $Entry }) {
        return
    }
    $updated = @($Entry) + $parts
    if ($DryRun) {
        Write-Host "[dry-run] add $Entry to user PATH"
        return
    }
    [Environment]::SetEnvironmentVariable("Path", ($updated -join ";"), "User")
    Refresh-ProcessPath
}

function Test-CommandAvailable([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Uv([string]$UvExe, [string]$UvDir) {
    if (Test-Path $UvExe) {
        return
    }

    if ($DryRun) {
        Write-Host "[dry-run] install uv into $UvDir"
        return
    }

    New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
    $installer = Join-Path $env:TEMP ("afkbot-uv-" + [guid]::NewGuid().ToString("N") + ".ps1")
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer
        $env:UV_UNMANAGED_INSTALL = $UvDir
        & $installer
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $installer
        Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path $UvExe)) {
        throw "uv installation completed but $UvExe was not created."
    }
}

function Invoke-Uv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExe,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($DryRun) {
        Write-Host "[dry-run] $UvExe $($Arguments -join ' ')"
        return
    }
    & $UvExe @Arguments
}

function Ensure-Venv([string]$UvExe, [string]$VenvDir) {
    Invoke-Uv -UvExe $UvExe -Arguments @("python", "install", "3.12")
    Invoke-Uv -UvExe $UvExe -Arguments @("venv", "--seed", "--allow-existing", "--python", "3.12", $VenvDir)
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

function Get-ArchiveUrl([string]$Value, [string]$Ref) {
    $normalized = $Value.Trim()
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
        return "$normalized/archive/$Ref.zip"
    }
    throw "Remote managed installs require a GitHub repository URL or a local source path."
}

function Get-ReleaseId {
    return (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmssfff")
}

function Install-SourceSnapshot([string]$AppRootDir) {
    $targetDir = Join-Path $AppRootDir (Get-ReleaseId)
    Invoke-Action -Description "create $AppRootDir" -Script {
        New-Item -ItemType Directory -Force -Path $AppRootDir | Out-Null
    }

    $localSource = Resolve-LocalSourcePath $RepoUrl
    if ($null -ne $localSource) {
        Invoke-Action -Description "copy local source $localSource -> $targetDir" -Script {
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            Copy-Item -Path (Join-Path $localSource "*") -Destination $targetDir -Recurse -Force
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir ".git")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir ".venv")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir ".pytest_cache")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir ".ruff_cache")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir ".mypy_cache")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir "build")
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $targetDir "dist")
        }
        return $targetDir
    }

    $archiveUrl = Get-ArchiveUrl -Value $RepoUrl -Ref $GitRef
    if ($DryRun) {
        Write-Host "[dry-run] download $archiveUrl"
        Write-Host "[dry-run] extract remote source into $targetDir"
        return $targetDir
    }

    $pythonExe = Join-Path $VenvDir "Scripts\python.exe"
    $pythonCode = @'
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

archive_url = os.environ["AFK_INSTALL_ARCHIVE_URL"]
target_dir = Path(os.environ["AFK_INSTALL_TARGET_DIR"]).resolve(strict=False)
temp_dir = Path(tempfile.mkdtemp(prefix="afkbot-source-")).resolve(strict=False)
archive_path = temp_dir / "source.zip"
extract_dir = temp_dir / "extract"
extract_dir.mkdir(parents=True, exist_ok=True)
try:
    with urlopen(archive_url, timeout=30) as response, archive_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    with zipfile.ZipFile(archive_path) as archive:
        root = extract_dir.resolve(strict=False)
        for member in archive.infolist():
            resolved_target = (extract_dir / member.filename).resolve(strict=False)
            if root not in resolved_target.parents and resolved_target != root:
                raise SystemExit(f"Remote archive contains unsafe path: {member.filename}")
        archive.extractall(extract_dir)
    entries = [item for item in extract_dir.iterdir() if item.is_dir()]
    if len(entries) != 1:
        raise SystemExit("Remote archive did not contain one source directory.")
    extracted_root = entries[0]
    if not (extracted_root / "pyproject.toml").exists():
        raise SystemExit("Remote archive is missing pyproject.toml.")
    if not (extracted_root / "afkbot").exists():
        raise SystemExit("Remote archive is missing the afkbot package.")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extracted_root), str(target_dir))
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
'@
    $env:AFK_INSTALL_ARCHIVE_URL = $archiveUrl
    $env:AFK_INSTALL_TARGET_DIR = $targetDir
    try {
        & $pythonExe -c $pythonCode
    } finally {
        Remove-Item Env:AFK_INSTALL_ARCHIVE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:AFK_INSTALL_TARGET_DIR -ErrorAction SilentlyContinue
    }
    return $targetDir
}

function Ensure-VenvAndPackage([string]$UvExe, [string]$VenvDir, [string]$AppDir) {
    Invoke-Uv -UvExe $UvExe -Arguments @("python", "install", "3.12")
    Invoke-Uv -UvExe $UvExe -Arguments @("venv", "--seed", "--allow-existing", "--python", "3.12", $VenvDir)

    $pythonExe = Join-Path $VenvDir "Scripts\python.exe"
    if (-not $DryRun -and -not (Test-Path $pythonExe)) {
        throw "Virtual environment python not found at $pythonExe."
    }

    Invoke-Action -Description "$pythonExe -m pip install --upgrade pip" -Script {
        & $pythonExe -m pip install --upgrade pip
    }
    Invoke-Action -Description "$pythonExe -m pip install --upgrade -e $AppDir" -Script {
        & $pythonExe -m pip install --upgrade -e $AppDir
    }
}

function Write-AfkShim([string]$ShimPath, [string]$InstallDirValue, [string]$RuntimeDir, [string]$AppDir, [string]$VenvDir) {
    $pythonExe = Join-Path $VenvDir "Scripts\python.exe"
    $shimDir = Split-Path -Parent $ShimPath
    $pythonCode = @'
import os
from pathlib import Path

from afkbot.services.managed_install import ManagedInstallContext, write_managed_launcher

context = ManagedInstallContext(
    install_dir=Path(os.environ["AFK_WRITE_INSTALL_DIR"]),
    runtime_dir=Path(os.environ["AFK_WRITE_RUNTIME_DIR"]),
    app_dir=Path(os.environ["AFK_WRITE_APP_DIR"]),
    source_url=os.environ["AFK_WRITE_SOURCE_URL"],
    source_ref=os.environ["AFK_WRITE_SOURCE_REF"],
)
write_managed_launcher(
    context=context,
    python_executable=Path(os.environ["AFK_WRITE_PYTHON"]),
    app_dir=Path(os.environ["AFK_WRITE_APP_DIR"]),
)
'@

    Invoke-Action -Description "write $ShimPath" -Script {
        New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
        $env:AFK_WRITE_INSTALL_DIR = $InstallDirValue
        $env:AFK_WRITE_RUNTIME_DIR = $RuntimeDir
        $env:AFK_WRITE_APP_DIR = $AppDir
        $env:AFK_WRITE_SOURCE_URL = $RepoUrl
        $env:AFK_WRITE_SOURCE_REF = $GitRef
        $env:AFK_WRITE_PYTHON = $pythonExe
        try {
            & $pythonExe -c $pythonCode
        } finally {
            Remove-Item Env:AFK_WRITE_INSTALL_DIR -ErrorAction SilentlyContinue
            Remove-Item Env:AFK_WRITE_RUNTIME_DIR -ErrorAction SilentlyContinue
            Remove-Item Env:AFK_WRITE_APP_DIR -ErrorAction SilentlyContinue
            Remove-Item Env:AFK_WRITE_SOURCE_URL -ErrorAction SilentlyContinue
            Remove-Item Env:AFK_WRITE_SOURCE_REF -ErrorAction SilentlyContinue
            Remove-Item Env:AFK_WRITE_PYTHON -ErrorAction SilentlyContinue
        }
    }
}

function Run-BootstrapSetup([string]$ShimPath) {
    if ($SkipSetup) {
        return
    }

    Invoke-Action -Description "$ShimPath setup --bootstrap-only --yes" -Script {
        & $ShimPath "setup" "--bootstrap-only" "--yes"
    }
}

function Remove-OldAppDirs([string]$AppRootDir, [string]$CurrentAppDir) {
    if (-not (Test-Path $AppRootDir)) {
        return
    }
    Get-ChildItem -Path $AppRootDir -Directory | ForEach-Object {
        if ($_.FullName -ieq $CurrentAppDir) {
            return
        }
        Invoke-Action -Description "remove stale source $_" -Script {
            Remove-Item -Recurse -Force $_.FullName
        }
    }
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "AFKBOT"
}

$AppRootDir = Join-Path $InstallDir "app"
$RuntimeDir = Join-Path $InstallDir "runtime"
$VenvDir = Join-Path $InstallDir "venv"
$UvDir = Join-Path $InstallDir ".uv"
$UvExe = Join-Path $UvDir "uv.exe"
$BinDir = Join-Path $InstallDir "bin"
$ShimPath = Join-Path $BinDir "afk.cmd"

Invoke-Action -Description "create $InstallDir" -Script {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
}
Ensure-Uv -UvExe $UvExe -UvDir $UvDir
Ensure-Venv -UvExe $UvExe -VenvDir $VenvDir
$CurrentAppDir = Install-SourceSnapshot -AppRootDir $AppRootDir
Ensure-VenvAndPackage -UvExe $UvExe -VenvDir $VenvDir -AppDir $CurrentAppDir
Invoke-Action -Description "create $RuntimeDir" -Script {
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
}
Write-AfkShim -ShimPath $ShimPath -InstallDirValue $InstallDir -RuntimeDir $RuntimeDir -AppDir $CurrentAppDir -VenvDir $VenvDir
Add-UserPathEntry -Entry $BinDir
Run-BootstrapSetup -ShimPath $ShimPath
Remove-OldAppDirs -AppRootDir $AppRootDir -CurrentAppDir $CurrentAppDir

Write-Host ""
Write-Host "AFKBOT install complete."
Write-Host "Install root: $InstallDir"
Write-Host "Runtime root: $RuntimeDir"
Write-Host "App source: $CurrentAppDir"
Write-Host "CLI: $ShimPath"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  afk setup"
Write-Host "  afk doctor"
Write-Host "  afk chat"
Write-Host ""
Write-Host "To update later, rerun this installer or use `afk update`."
