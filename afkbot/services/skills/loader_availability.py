"""Availability checks for AFKBOT markdown skills."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

from afkbot.services.skills.loader_contracts import SkillManifest
from afkbot.services.skills.loader_manifest import build_manifest, parse_list
from afkbot.services.skills.markdown import FrontmatterValue, parse_frontmatter
from afkbot.settings import Settings


def check_skill_availability(
    *,
    path: Path,
    settings: Settings,
    metadata: dict[str, FrontmatterValue] | None = None,
    manifest: SkillManifest | None = None,
    manifest_errors: tuple[str, ...] = (),
) -> tuple[bool, set[str], set[str]]:
    """Evaluate os/bin/env/python requirements for one skill."""

    if not path.exists():
        return False, {"missing_file"}, set()

    if metadata is None:
        metadata = parse_frontmatter(path.read_text(encoding="utf-8"))
    if manifest is None:
        manifest = build_manifest(
            name=path.parent.name,
            content=path.read_text(encoding="utf-8"),
            metadata=metadata,
        )

    missing: set[str] = set()
    suggested_missing: set[str] = set()

    allowed_os = parse_list(metadata.get("os", ""))
    if allowed_os and platform_name() not in allowed_os:
        missing.add("os")

    for binary in manifest.requires_bins:
        if shutil.which(binary) is None:
            missing.add(f"bin:{binary}")

    for binary in manifest.suggested_bins:
        if shutil.which(binary) is None:
            suggested_missing.add(f"bin:{binary}")

    for env_name in manifest.requires_env:
        if not has_required_env(settings, env_name):
            missing.add(f"env:{env_name}")

    for package_name in manifest.requires_python_packages:
        if not has_python_package(package_name):
            missing.add(f"python:{package_name}")

    for manifest_error in manifest_errors:
        missing.add(f"manifest:{manifest_error}")

    return not missing, missing, suggested_missing


def has_required_env(settings: Settings, env_name: str) -> bool:
    """Return whether one required env is available via process env or resolved settings."""

    raw = os.getenv(env_name)
    if isinstance(raw, str) and raw.strip():
        return True
    if not env_name.startswith("AFKBOT_"):
        return False
    field_name = env_name.removeprefix("AFKBOT_").lower()
    if not hasattr(settings, field_name):
        return False
    value = getattr(settings, field_name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def has_python_package(package_name: str) -> bool:
    """Return whether one Python distribution is installed in the current runtime."""

    try:
        importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def platform_name() -> str:
    """Return the normalized current platform name."""

    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform
