"""Version helpers for CLI and runtime diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import subprocess
import tomllib

_PACKAGE_NAME = "afkbotio"
_DEFAULT_VERSION = "0.0.0"


@dataclass(frozen=True, slots=True)
class CliVersionInfo:
    """Resolved AFKBOT version metadata for operator-facing CLI output."""

    version: str
    git_sha: str | None = None
    git_branch: str | None = None
    dirty: bool = False

    def render(self) -> str:
        """Render one compact, human-readable CLI version line."""

        if self.git_sha is None:
            return f"afk {self.version}"

        details = [f"git {self.git_sha}"]
        if self.git_branch:
            details.append(f"on {self.git_branch}")
        if self.dirty:
            details.append("dirty")
        return f"afk {self.version} ({', '.join(details)})"


def load_cli_version_info(*, root_dir: Path | None = None) -> CliVersionInfo:
    """Resolve package version plus best-effort git checkout metadata."""

    resolved_root = root_dir or _default_project_root()
    version = _resolve_package_version(resolved_root)
    git_sha, git_branch, dirty = _resolve_git_metadata(resolved_root)
    return CliVersionInfo(
        version=version,
        git_sha=git_sha,
        git_branch=git_branch,
        dirty=dirty,
    )


def _default_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_package_version(root_dir: Path) -> str:
    local_version = _read_pyproject_version(root_dir)
    if local_version is not None:
        return local_version
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _DEFAULT_VERSION


def _read_pyproject_version(root_dir: Path) -> str | None:
    pyproject_path = root_dir / "pyproject.toml"
    if not pyproject_path.exists():
        return None

    with pyproject_path.open("rb") as handle:
        payload = tomllib.load(handle)
    project = payload.get("project")
    if not isinstance(project, dict):
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version.strip() else None


def _resolve_git_metadata(root_dir: Path) -> tuple[str | None, str | None, bool]:
    if not (root_dir / ".git").exists():
        return None, None, False

    git_sha = _run_git(root_dir, "rev-parse", "--short", "HEAD")
    if git_sha is None:
        return None, None, False

    git_branch = _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
    dirty_output = _run_git(root_dir, "status", "--short", "--untracked-files=no")
    return (
        git_sha,
        None if git_branch in {None, "", "HEAD"} else git_branch,
        bool(dirty_output),
    )


def _run_git(root_dir: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root_dir), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


__all__ = ["CliVersionInfo", "load_cli_version_info"]
