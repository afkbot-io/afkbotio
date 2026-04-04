# AFKBOT installer for Windows
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$RepoUrl = "https://github.com/afkbot-io/afkbotio.git",
    [string]$GitRef = "main",
    [string]$Lang = "",
    [switch]$SkipSetup,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:ResolvedInstallLang = "en"

if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
    Write-Warning "-InstallDir is ignored by the uv tool installer."
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

function Resolve-InstallLanguage {
    param([string]$Requested)

    $requestedValue = ""
    if ($null -ne $Requested) {
        $requestedValue = [string]$Requested
    }
    $normalized = $requestedValue.Trim().ToLowerInvariant().Replace("-", "_")
    if ($normalized -in @("ru", "russian", "ru_ru")) {
        return "ru"
    }
    if (-not [string]::IsNullOrWhiteSpace($normalized) -and $normalized -notin @("en", "english", "en_us", "en_gb")) {
        throw "-Lang must be one of: en, ru."
    }

    $candidates = @(
        $env:LC_ALL,
        $env:LC_MESSAGES,
        $env:LANG,
        $PSUICulture,
        [System.Globalization.CultureInfo]::CurrentUICulture.Name
    )
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $resolved = $candidate.Trim().ToLowerInvariant().Replace("-", "_")
        if ($resolved.StartsWith("ru")) {
            return "ru"
        }
    }
    return "en"
}

function Get-LocalizedText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$English,
        [Parameter(Mandatory = $true)]
        [string]$Russian
    )

    if ($script:ResolvedInstallLang -eq "ru") {
        return $Russian
    }
    return $English
}

function Write-Localized {
    param(
        [Parameter(Mandatory = $true)]
        [string]$English,
        [Parameter(Mandatory = $true)]
        [string]$Russian
    )

    Write-Host (Get-LocalizedText -English $English -Russian $Russian)
}

function Get-UserBinDir {
    if (-not [string]::IsNullOrWhiteSpace($env:XDG_BIN_HOME)) {
        return $env:XDG_BIN_HOME
    }
    return Join-Path (Get-HomeDir) ".local\bin"
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
    if ($normalized.StartsWith("http://github.com/")) {
        $normalized = "https://github.com/" + $normalized.Substring("http://github.com/".Length)
    }
    if ($normalized.StartsWith("https://www.github.com/")) {
        $normalized = "https://github.com/" + $normalized.Substring("https://www.github.com/".Length)
    }
    if ($normalized.StartsWith("http://www.github.com/")) {
        $normalized = "https://github.com/" + $normalized.Substring("http://www.github.com/".Length)
    }
    if ($normalized.EndsWith(".git")) {
        $normalized = $normalized.Substring(0, $normalized.Length - 4)
    }
    $normalized = $normalized.TrimEnd("/")
    if ($normalized.StartsWith("https://github.com/")) {
        return @{
            Mode = "archive"
            Value = "$normalized/archive/$GitRef.tar.gz"
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

function Get-AfkToolPath([string]$ToolBinDir) {
    $exePath = Join-Path $ToolBinDir "afk.exe"
    if (Test-Path $exePath) {
        return $exePath
    }
    $cmdPath = Join-Path $ToolBinDir "afk.cmd"
    return $cmdPath
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
        Write-Warning (
            Get-LocalizedText `
                -English "uv tool update-shell failed; reopen the terminal after install if 'afk' is not yet visible." `
                -Russian "Не удалось выполнить uv tool update-shell; после установки откройте терминал заново, если 'afk' ещё не появился."
        )
    }
}

$script:ResolvedInstallLang = Resolve-InstallLanguage -Requested $Lang
$uvExe = Get-UvExePath
Ensure-Uv -UvExe $uvExe
$toolSource = Get-ToolSource
Install-AfkTool -UvExe $uvExe -ToolSource $toolSource
Update-ToolShell -UvExe $uvExe

$toolBinDir = Get-ToolBinDir -UvExe $uvExe
$afkCmd = Get-AfkToolPath -ToolBinDir $toolBinDir
$env:Path = "$toolBinDir;$env:Path"

if (-not $SkipSetup) {
    $previousInstallSourceMode = $env:AFKBOT_INSTALL_SOURCE_MODE
    $previousInstallSourceSpec = $env:AFKBOT_INSTALL_SOURCE_SPEC
    try {
        $env:AFKBOT_INSTALL_SOURCE_MODE = [string]$toolSource.Mode
        $env:AFKBOT_INSTALL_SOURCE_SPEC = [string]$toolSource.Value
        [void](Invoke-NativeCommand -Description "$afkCmd setup --bootstrap-only --yes --lang $script:ResolvedInstallLang" -Script {
            & $afkCmd "setup" "--bootstrap-only" "--yes" "--lang" $script:ResolvedInstallLang
        })
    } finally {
        if ([string]::IsNullOrWhiteSpace($previousInstallSourceMode)) {
            Remove-Item Env:AFKBOT_INSTALL_SOURCE_MODE -ErrorAction SilentlyContinue
        } else {
            $env:AFKBOT_INSTALL_SOURCE_MODE = $previousInstallSourceMode
        }
        if ([string]::IsNullOrWhiteSpace($previousInstallSourceSpec)) {
            Remove-Item Env:AFKBOT_INSTALL_SOURCE_SPEC -ErrorAction SilentlyContinue
        } else {
            $env:AFKBOT_INSTALL_SOURCE_SPEC = $previousInstallSourceSpec
        }
    }
}

$legacyManagedBin = Join-Path (Get-LegacyInstallDir) "bin"
Remove-UserPathEntry -Entry $legacyManagedBin

Write-Host ""
Write-Localized -English "AFKBOT install complete." -Russian "Установка AFKBOT завершена."
Write-Localized -English "Tool source: $($toolSource.Value)" -Russian "Источник установки: $($toolSource.Value)"
Write-Host "uv: $uvExe"
Write-Host "CLI: $afkCmd"
Write-Host ""
Write-Localized `
    -English "Recommended next step: open a new terminal window." `
    -Russian "Рекомендуемый следующий шаг: откройте новый терминал."
Write-Host ""
Write-Localized -English "Then run:" -Russian "Затем выполните:"
Write-Host "  afk setup"
Write-Host ""
Write-Localized `
    -English "After `afk setup`, AFKBOT will tell you to run `afk doctor` and then `afk chat`." `
    -Russian "После `afk setup` AFKBOT подскажет выполнить `afk doctor`, а затем `afk chat`."
Write-Host ""
Write-Localized -English 'To update later, run `afk update`.' -Russian 'Чтобы обновиться позже, выполните `afk update`.'
