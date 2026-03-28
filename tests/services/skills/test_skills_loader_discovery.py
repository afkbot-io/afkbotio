"""Discovery and routing tests for the markdown skills loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.services.skills._loader_harness import build_loader, write_manifest, write_skill


async def test_skills_loader_loads_core_and_profile_and_always(tmp_path: Path) -> None:
    """Loader should merge core and profile skills with always skill."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# core")
    write_skill(tmp_path, "profiles/p1/skills/custom", "# custom")
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("p1")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "custom" in skill_map
    assert "security-secrets" in skill_map
    assert skill_map["security-secrets"].available is True
    assert await loader.load_skill("custom", "p1") == "# custom"


async def test_skills_loader_prefers_frontmatter_description_for_summary(tmp_path: Path) -> None:
    """Skill summary should come from frontmatter description when present."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "afkbot/skills/described",
        "---\nname: described\ndescription: \"Use this exact description for routing.\"\n---\n# Wrong heading\nBody text.",
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    assert {item.name: item for item in skills}["described"].summary == (
        "Use this exact description for routing."
    )


async def test_skills_loader_rejects_path_traversal(tmp_path: Path) -> None:
    """Loader should reject invalid skill names that may traverse paths."""

    # Arrange
    loader = build_loader(tmp_path)

    # Act / Assert
    with pytest.raises(ValueError):
        await loader.load_skill("../etc/passwd", "p1")


async def test_skills_loader_rejects_invalid_profile_id(tmp_path: Path) -> None:
    """Profile id traversal attempts must be rejected for list and load calls."""

    # Arrange
    loader = build_loader(tmp_path)

    # Act / Assert
    with pytest.raises(ValueError):
        await loader.list_skills("../outside")
    with pytest.raises(ValueError):
        await loader.load_skill("security-secrets", "../outside")


async def test_skills_loader_skips_out_of_scope_symlink_on_list_and_rejects_load(
    tmp_path: Path,
) -> None:
    """Symlinked skills outside the root must be hidden from list and fail on load."""

    # Arrange
    unsafe_dir = tmp_path / "afkbot/skills/unsafe-skill"
    unsafe_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside/unsafe-skill/SKILL.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("# outside", encoding="utf-8")
    try:
        (unsafe_dir / "SKILL.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    names = {item.name for item in skills}
    assert "unsafe-skill" not in names
    with pytest.raises(ValueError):
        await loader.load_skill("unsafe-skill", "default")


async def test_skills_loader_ignores_nested_entries_and_list_matches_load_contract(
    tmp_path: Path,
) -> None:
    """Discovery must be one-level; listed skills must be resolvable by load."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/analysis", "# core analysis")
    write_skill(tmp_path, "afkbot/skills/group/nested", "# nested core")
    write_skill(tmp_path, "profiles/p1/skills/analysis", "# profile analysis")
    write_skill(tmp_path, "profiles/p1/skills/team/ignored", "# nested profile")
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("p1")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "analysis" in skill_map
    assert skill_map["analysis"].origin == "profile"
    assert "nested" not in skill_map
    assert "ignored" not in skill_map
    assert await loader.load_skill("analysis", "p1") == "# profile analysis"
    with pytest.raises(FileNotFoundError):
        await loader.load_skill("nested", "p1")


async def test_skills_loader_reports_mandatory_security_secrets_unavailable_when_unsafe(
    tmp_path: Path,
) -> None:
    """Mandatory security-secrets should fail closed in list without raising."""

    # Arrange
    security_dir = tmp_path / "afkbot/skills/security-secrets"
    security_dir.mkdir(parents=True)
    outside_file = tmp_path / "outside/security-secrets/SKILL.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("# outside security", encoding="utf-8")
    try:
        (security_dir / "SKILL.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "security-secrets" in skill_map
    assert skill_map["security-secrets"].available is False
    assert "unsafe_path" in skill_map["security-secrets"].missing_requirements
    with pytest.raises(ValueError):
        await loader.load_skill("security-secrets", "default")


async def test_skills_loader_uses_core_mandatory_security_when_profile_override_unavailable(
    tmp_path: Path,
) -> None:
    """Mandatory security-secrets should fall back to core when profile override is unavailable."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# core security")
    write_skill(
        tmp_path,
        "profiles/p1/skills/security-secrets",
        "---\nrequires_env: AFKBOT_TEST_MUST_NOT_EXIST_SECURITY_SECRETS\n---\n# profile security",
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("p1")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "security-secrets" in skill_map
    assert skill_map["security-secrets"].origin == "core"
    assert skill_map["security-secrets"].available is True
    assert skill_map["security-secrets"].missing_requirements == ()
    assert await loader.load_skill("security-secrets", "p1") == "# core security"


async def test_skills_loader_profile_listing_keeps_profile_security_override(tmp_path: Path) -> None:
    """list_profile_skills should keep profile-origin security-secrets entry."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# core security")
    write_skill(tmp_path, "profiles/p1/skills/security-secrets", "# profile security")
    loader = build_loader(tmp_path)

    # Act
    merged = await loader.list_skills("p1")

    # Assert
    merged_map = {item.name: item for item in merged}
    assert merged_map["security-secrets"].origin == "core"
    profile_only = await loader.list_profile_skills("p1")
    assert {item.name: item for item in profile_only}["security-secrets"].origin == "profile"


async def test_skills_loader_marks_invalid_afkbot_manifest_unavailable(tmp_path: Path) -> None:
    """Broken AFKBOT manifest should not silently fall back to healthy availability."""

    # Arrange
    skill_dir = write_skill(tmp_path, "afkbot/skills/pdf", "# pdf\nUse pdf workflow.")
    write_manifest(
        skill_dir,
        'manifest_version = 1\nname = "pdf"\nexecution_mode = ',
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    info = {item.name: item for item in skills}["pdf"]
    assert info.available is False
    assert info.manifest_valid is False
    assert "parse_error" in info.manifest_errors
    assert "manifest:parse_error" in info.missing_requirements
