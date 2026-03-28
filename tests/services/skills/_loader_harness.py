"""Shared helpers for skills-loader tests."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.skills.skills import SkillLoader
from afkbot.settings import Settings


def build_loader(
    root_dir: Path,
    *,
    credentials_master_keys: str | None = None,
) -> SkillLoader:
    """Build a loader for one isolated temporary root."""

    if credentials_master_keys is None:
        settings = Settings(root_dir=root_dir)
    else:
        settings = Settings(
            root_dir=root_dir,
            credentials_master_keys=credentials_master_keys,
        )
    return SkillLoader(settings)


def write_skill(root_dir: Path, relative_dir: str, content: str) -> Path:
    """Create one skill directory with a `SKILL.md` file and return the directory."""

    skill_dir = root_dir / relative_dir
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def write_manifest(skill_dir: Path, content: str) -> Path:
    """Create one AFKBOT manifest alongside an existing skill directory."""

    manifest_path = skill_dir / "AFKBOT.skill.toml"
    manifest_path.write_text(content, encoding="utf-8")
    return manifest_path
