"""Compatibility exports for the AFKBOT markdown skill loader."""

from __future__ import annotations

from afkbot.services.skills.loader_contracts import (
    SkillExecutionMode,
    SkillInfo,
    SkillManifest,
    SkillManifestAction,
    SkillManifestMaterialization,
)
from afkbot.services.skills.loader_service import SkillLoader

__all__ = [
    "SkillExecutionMode",
    "SkillInfo",
    "SkillLoader",
    "SkillManifest",
    "SkillManifestAction",
    "SkillManifestMaterialization",
]
