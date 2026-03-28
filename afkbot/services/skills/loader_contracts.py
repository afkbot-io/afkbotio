"""Contracts used by the AFKBOT markdown skill loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SkillExecutionMode = Literal["advisory", "executable", "dispatch"]
SkillManifestAction = Literal["created", "repaired", "overwritten", "skipped"]


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """Machine-readable skill metadata extracted from markdown frontmatter."""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    app_names: tuple[str, ...] = ()
    preferred_tool_order: tuple[str, ...] = ()
    always_on: bool = False
    execution_mode: SkillExecutionMode = "advisory"
    requires_bins: tuple[str, ...] = ()
    suggested_bins: tuple[str, ...] = ()
    requires_env: tuple[str, ...] = ()
    requires_python_packages: tuple[str, ...] = ()
    source_kind: str = ""
    source_id: str = ""
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """Skill metadata exposed to other services."""

    name: str
    path: Path
    origin: str
    available: bool
    missing_requirements: tuple[str, ...]
    missing_suggested_requirements: tuple[str, ...]
    summary: str
    aliases: tuple[str, ...]
    manifest: SkillManifest
    manifest_path: Path | None = None
    manifest_valid: bool = True
    manifest_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillManifestMaterialization:
    """Result of creating, repairing, or reusing one AFKBOT skill manifest."""

    path: Path
    action: SkillManifestAction
    manifest: SkillManifest
