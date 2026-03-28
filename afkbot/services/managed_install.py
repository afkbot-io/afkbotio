"""Helpers for managed AFKBOT installs that keep runtime state outside the app source tree."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import urlopen


MANAGED_INSTALL_DIR_ENV = "AFKBOT_MANAGED_INSTALL_DIR"
MANAGED_RUNTIME_DIR_ENV = "AFKBOT_ROOT_DIR"
MANAGED_APP_DIR_ENV = "AFKBOT_MANAGED_APP_DIR"
MANAGED_SOURCE_URL_ENV = "AFKBOT_MANAGED_SOURCE_URL"
MANAGED_SOURCE_REF_ENV = "AFKBOT_MANAGED_SOURCE_REF"
MANAGED_METADATA_PATH_ENV = "AFKBOT_MANAGED_METADATA_PATH"
MANAGED_METADATA_FILE_NAME = "managed-install.json"

_LOCAL_COPY_IGNORE = (
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "build",
    "dist",
)


@dataclass(frozen=True, slots=True)
class ManagedInstallContext:
    """Resolved managed-install metadata injected by the launch wrapper."""

    install_dir: Path
    runtime_dir: Path
    app_dir: Path
    source_url: str
    source_ref: str

    @property
    def app_root(self) -> Path:
        """Return the parent directory that stores versioned source snapshots."""

        return self.install_dir / "app"

    @property
    def bin_dir(self) -> Path:
        """Return the managed launcher directory."""

        return self.install_dir / "bin"

    @property
    def metadata_path(self) -> Path:
        """Return the persisted managed-install metadata file path."""

        return self.install_dir / MANAGED_METADATA_FILE_NAME


def resolve_managed_install_context() -> ManagedInstallContext | None:
    """Return managed-install metadata from wrapper-provided environment or metadata file."""

    raw_install_dir = str(os.getenv(MANAGED_INSTALL_DIR_ENV) or "").strip()
    raw_runtime_dir = str(os.getenv(MANAGED_RUNTIME_DIR_ENV) or "").strip()
    raw_app_dir = str(os.getenv(MANAGED_APP_DIR_ENV) or "").strip()
    raw_source_url = str(os.getenv(MANAGED_SOURCE_URL_ENV) or "").strip()
    raw_source_ref = str(os.getenv(MANAGED_SOURCE_REF_ENV) or "").strip()
    if not all((raw_install_dir, raw_runtime_dir, raw_app_dir, raw_source_url, raw_source_ref)):
        return _resolve_context_from_metadata()
    return ManagedInstallContext(
        install_dir=Path(raw_install_dir).resolve(strict=False),
        runtime_dir=Path(raw_runtime_dir).resolve(strict=False),
        app_dir=Path(raw_app_dir).resolve(strict=False),
        source_url=raw_source_url,
        source_ref=raw_source_ref,
    )


def build_release_id() -> str:
    """Return one filesystem-safe release id for a new managed source snapshot."""

    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + f"-{os.getpid()}"


def build_next_app_dir(context: ManagedInstallContext) -> Path:
    """Return the destination directory for the next managed source snapshot."""

    return context.app_root / build_release_id()


def stage_source_snapshot(context: ManagedInstallContext) -> Path:
    """Materialize the requested source snapshot into a temporary directory and return it."""

    source_url = context.source_url
    local_path = _resolve_local_source_path(source_url)
    if local_path is not None:
        return _stage_local_source(local_path)
    return _stage_remote_archive(source_url=source_url, source_ref=context.source_ref)


def cleanup_staged_source(staged_root: Path) -> None:
    """Best-effort cleanup of a temporary staged source directory tree."""

    for candidate in (staged_root, *staged_root.parents):
        if candidate.name.startswith("afkbot-source-"):
            shutil.rmtree(candidate, ignore_errors=True)
            return


def write_managed_launcher(
    *,
    context: ManagedInstallContext,
    python_executable: Path,
    app_dir: Path,
) -> Path:
    """Write the platform launcher that pins runtime/app metadata for future commands."""

    context.bin_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = _write_metadata_file(context=context, app_dir=app_dir)
    metadata_relative = _relative_launcher_path(base_dir=context.bin_dir, target=metadata_path)
    runtime_relative = _relative_launcher_path(
        base_dir=context.bin_dir,
        target=context.runtime_dir,
    )
    python_relative = _relative_launcher_path(
        base_dir=context.bin_dir,
        target=python_executable,
        resolve_target=False,
    )
    if os.name == "nt":
        launcher_path = context.bin_dir / "afk.cmd"
        launcher_path.write_text(
            _render_windows_launcher(
                runtime_relative=runtime_relative,
                metadata_relative=metadata_relative,
                python_relative=python_relative,
            ),
            encoding="utf-8",
        )
        return launcher_path

    launcher_path = context.bin_dir / "afk"
    launcher_path.write_text(
        _render_unix_launcher(
            runtime_relative=runtime_relative,
            metadata_relative=metadata_relative,
            python_relative=python_relative,
        ),
        encoding="utf-8",
    )
    launcher_path.chmod(0o755)
    return launcher_path


def pick_convenience_launcher_path(
    *,
    launcher_path: Path,
    path_env: str,
) -> Path | None:
    """Return one writable in-PATH location for an immediate `afk` symlink."""

    launcher_resolved = launcher_path.resolve(strict=False)
    launcher_dir = launcher_path.parent.resolve(strict=False)
    seen_dirs: set[Path] = set()
    for raw_dir in path_env.split(os.pathsep):
        normalized = raw_dir.strip()
        if not normalized:
            continue
        candidate_dir = Path(normalized).expanduser().resolve(strict=False)
        if candidate_dir in seen_dirs:
            continue
        seen_dirs.add(candidate_dir)
        if candidate_dir == launcher_dir:
            continue
        if not candidate_dir.exists() or not candidate_dir.is_dir():
            continue
        if not os.access(candidate_dir, os.W_OK | os.X_OK):
            continue
        candidate_path = candidate_dir / launcher_path.name
        if candidate_path.is_symlink():
            if not candidate_path.exists():
                return candidate_path
            if candidate_path.resolve(strict=False) == launcher_resolved:
                return candidate_path
            continue
        if candidate_path.exists():
            continue
        return candidate_path
    return None


def prune_stale_app_dirs(
    *,
    context: ManagedInstallContext,
    keep_paths: tuple[Path, ...],
) -> None:
    """Best-effort removal of stale managed source snapshots."""

    keep = {path.resolve(strict=False) for path in keep_paths}
    app_root = context.app_root
    if not app_root.exists():
        return
    for candidate in app_root.iterdir():
        resolved = candidate.resolve(strict=False)
        if resolved in keep:
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)


def _resolve_local_source_path(source_url: str) -> Path | None:
    parsed = urlparse(source_url)
    if parsed.scheme == "file":
        return Path(parsed.path).resolve(strict=False)
    candidate = Path(source_url)
    if candidate.exists():
        return candidate.resolve(strict=False)
    return None


def _stage_local_source(source_path: Path) -> Path:
    if not source_path.exists() or not source_path.is_dir():
        raise ValueError(f"Managed source path does not exist: {source_path}")
    if not (source_path / "pyproject.toml").exists():
        raise ValueError(f"Managed source path is missing pyproject.toml: {source_path}")
    if not (source_path / "afkbot").exists():
        raise ValueError(f"Managed source path is missing afkbot package: {source_path}")
    temp_dir = Path(tempfile.mkdtemp(prefix="afkbot-source-")).resolve(strict=False)
    target = temp_dir / "source"
    shutil.copytree(
        source_path,
        target,
        ignore=shutil.ignore_patterns(*_LOCAL_COPY_IGNORE),
    )
    return target


def _stage_remote_archive(*, source_url: str, source_ref: str) -> Path:
    archive_url = _build_archive_url(source_url=source_url, source_ref=source_ref)
    temp_dir = Path(tempfile.mkdtemp(prefix="afkbot-source-")).resolve(strict=False)
    archive_path = temp_dir / "source.tar.gz"
    with urlopen(archive_url, timeout=30) as response, archive_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    extract_dir = temp_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        _safe_extract_tar(archive=archive, destination=extract_dir)
    entries = [item for item in extract_dir.iterdir() if item.is_dir()]
    if len(entries) != 1:
        raise ValueError(f"Managed source archive did not contain one root directory: {archive_url}")
    root = entries[0]
    if not (root / "pyproject.toml").exists():
        raise ValueError(f"Managed source archive is missing pyproject.toml: {archive_url}")
    if not (root / "afkbot").exists():
        raise ValueError(f"Managed source archive is missing afkbot package: {archive_url}")
    return root


def _build_archive_url(*, source_url: str, source_ref: str) -> str:
    normalized = source_url.strip()
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    normalized = normalized.removesuffix(".git").rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"github.com", "www.github.com"}:
        raise ValueError(f"Managed remote installs require a GitHub repository URL: {source_url}")
    if not source_ref.strip():
        raise ValueError("Managed source ref is required")
    return f"{normalized}/archive/{source_ref}.tar.gz"


def _safe_extract_tar(*, archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="data")
    except tarfile.FilterError as exc:
        member_name = getattr(getattr(exc, "tarinfo", None), "name", "<unknown>")
        raise ValueError(f"Managed source archive contains unsafe path: {member_name}") from exc


def _resolve_context_from_metadata() -> ManagedInstallContext | None:
    raw_metadata_path = str(os.getenv(MANAGED_METADATA_PATH_ENV) or "").strip()
    if not raw_metadata_path:
        return None
    metadata_path = Path(raw_metadata_path).expanduser()
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    try:
        install_dir = Path(str(payload["install_dir"])).resolve(strict=False)
        runtime_dir = Path(str(payload["runtime_dir"])).resolve(strict=False)
        app_dir = Path(str(payload["app_dir"])).resolve(strict=False)
        source_url = str(payload["source_url"]).strip()
        source_ref = str(payload["source_ref"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not all((source_url, source_ref)):
        return None
    return ManagedInstallContext(
        install_dir=install_dir,
        runtime_dir=runtime_dir,
        app_dir=app_dir,
        source_url=source_url,
        source_ref=source_ref,
    )


def _write_metadata_file(*, context: ManagedInstallContext, app_dir: Path) -> Path:
    payload = {
        "install_dir": str(context.install_dir.resolve(strict=False)),
        "runtime_dir": str(context.runtime_dir.resolve(strict=False)),
        "app_dir": str(app_dir.resolve(strict=False)),
        "source_url": context.source_url,
        "source_ref": context.source_ref,
    }
    context.metadata_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return context.metadata_path


def _relative_launcher_path(
    *,
    base_dir: Path,
    target: Path,
    resolve_target: bool = True,
) -> str:
    return os.path.relpath(
        str(target.resolve(strict=False) if resolve_target else target),
        start=str(base_dir.resolve(strict=False)),
    )


def _render_unix_launcher(
    *,
    runtime_relative: str,
    metadata_relative: str,
    python_relative: str,
) -> str:
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            'script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
            f'export {MANAGED_RUNTIME_DIR_ENV}="${{script_dir}}/{runtime_relative}"',
            f'export {MANAGED_METADATA_PATH_ENV}="${{script_dir}}/{metadata_relative}"',
            f'cd "${{script_dir}}/{runtime_relative}"',
            f'exec "${{script_dir}}/{python_relative}" -m afkbot.cli.main "$@"',
            "",
        ]
    )


def _render_windows_launcher(
    *,
    runtime_relative: str,
    metadata_relative: str,
    python_relative: str,
) -> str:
    return "\n".join(
        [
            "@echo off",
            "setlocal",
            f'set "{MANAGED_RUNTIME_DIR_ENV}=%~dp0{runtime_relative}"',
            f'set "{MANAGED_METADATA_PATH_ENV}=%~dp0{metadata_relative}"',
            f'pushd "%~dp0{runtime_relative}" >nul',
            f'"%~dp0{python_relative}" -m afkbot.cli.main %*',
            'set "exit_code=%ERRORLEVEL%"',
            "popd >nul",
            "exit /b %exit_code%",
            "",
        ]
    )
