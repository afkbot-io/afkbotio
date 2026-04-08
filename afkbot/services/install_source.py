"""Installer source metadata helpers shared by setup and update flows."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal
from collections.abc import Mapping


INSTALL_SOURCE_MODE_ENV = "AFKBOT_INSTALL_SOURCE_MODE"
INSTALL_SOURCE_SPEC_ENV = "AFKBOT_INSTALL_SOURCE_SPEC"
INSTALL_SOURCE_RESOLVED_TARGET_ENV = "AFKBOT_INSTALL_SOURCE_RESOLVED_TARGET"
INSTALL_SOURCE_MODE_CONFIG_KEY = "install_source_mode"
INSTALL_SOURCE_SPEC_CONFIG_KEY = "install_source_spec"
INSTALL_SOURCE_RESOLVED_TARGET_CONFIG_KEY = "install_source_resolved_target"
DEFAULT_PACKAGE_SOURCE_SPEC = "afkbotio"
DEFAULT_HOSTED_ARCHIVE_SOURCE_SPEC = "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz"
_VALID_INSTALL_SOURCE_MODES = frozenset({"editable", "archive", "package"})


@dataclass(frozen=True, slots=True)
class InstallSource:
    """Normalized installer source that can be replayed by `afk update`."""

    mode: Literal["editable", "archive", "package"]
    spec: str


def default_package_install_source() -> InstallSource:
    """Return the canonical package install source used by hosted installs."""

    return InstallSource(mode="package", spec=DEFAULT_PACKAGE_SOURCE_SPEC)


def default_hosted_archive_install_source() -> InstallSource:
    """Return the canonical hosted archive source used by the install scripts."""

    return InstallSource(mode="archive", spec=DEFAULT_HOSTED_ARCHIVE_SOURCE_SPEC)


def read_install_source_from_env() -> InstallSource | None:
    """Read installer source metadata from one command-scoped environment."""

    return _normalize_install_source(
        mode=os.getenv(INSTALL_SOURCE_MODE_ENV),
        spec=os.getenv(INSTALL_SOURCE_SPEC_ENV),
    )


def read_install_source_resolved_target_from_env() -> str | None:
    """Read one resolved installer target from the command environment."""

    value = str(os.getenv(INSTALL_SOURCE_RESOLVED_TARGET_ENV) or "").strip()
    return value or None


def read_install_source_from_runtime_config(payload: Mapping[str, object]) -> InstallSource | None:
    """Read persisted installer source metadata from runtime config payload."""

    return _normalize_install_source(
        mode=payload.get(INSTALL_SOURCE_MODE_CONFIG_KEY),
        spec=payload.get(INSTALL_SOURCE_SPEC_CONFIG_KEY),
    )


def read_install_source_resolved_target_from_runtime_config(payload: Mapping[str, object]) -> str | None:
    """Read one persisted resolved installer target from runtime config."""

    value = str(payload.get(INSTALL_SOURCE_RESOLVED_TARGET_CONFIG_KEY) or "").strip()
    return value or None


def install_source_runtime_payload(
    install_source: InstallSource | None,
    *,
    resolved_target: str | None = None,
) -> dict[str, object]:
    """Return runtime-config fields for one persisted installer source."""

    if install_source is None:
        return {}
    payload: dict[str, object] = {
        INSTALL_SOURCE_MODE_CONFIG_KEY: install_source.mode,
        INSTALL_SOURCE_SPEC_CONFIG_KEY: install_source.spec,
    }
    if resolved_target is not None:
        payload[INSTALL_SOURCE_RESOLVED_TARGET_CONFIG_KEY] = resolved_target
    return payload


def build_uv_tool_install_command(
    *,
    uv_executable: Path,
    install_source: InstallSource,
) -> list[str]:
    """Build the `uv tool install` command for one persisted install source."""

    command = [
        str(uv_executable),
        "tool",
        "install",
        "--python",
        "3.12",
        "--reinstall",
    ]
    if install_source.mode == "editable":
        command.append("--editable")
    command.append(install_source.spec)
    return command


def _normalize_install_source(*, mode: object, spec: object) -> InstallSource | None:
    normalized_mode = str(mode or "").strip().lower()
    normalized_spec = str(spec or "").strip()
    if normalized_mode not in _VALID_INSTALL_SOURCE_MODES or not normalized_spec:
        return None
    return InstallSource(
        mode=normalized_mode,  # type: ignore[arg-type]
        spec=normalized_spec,
    )


__all__ = [
    "DEFAULT_PACKAGE_SOURCE_SPEC",
    "DEFAULT_HOSTED_ARCHIVE_SOURCE_SPEC",
    "INSTALL_SOURCE_MODE_CONFIG_KEY",
    "INSTALL_SOURCE_MODE_ENV",
    "INSTALL_SOURCE_RESOLVED_TARGET_CONFIG_KEY",
    "INSTALL_SOURCE_RESOLVED_TARGET_ENV",
    "INSTALL_SOURCE_SPEC_CONFIG_KEY",
    "INSTALL_SOURCE_SPEC_ENV",
    "InstallSource",
    "build_uv_tool_install_command",
    "default_hosted_archive_install_source",
    "default_package_install_source",
    "install_source_runtime_payload",
    "read_install_source_from_env",
    "read_install_source_from_runtime_config",
    "read_install_source_resolved_target_from_env",
    "read_install_source_resolved_target_from_runtime_config",
]
