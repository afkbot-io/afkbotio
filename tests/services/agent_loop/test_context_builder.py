"""Tests for context builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.skills.skills import SkillLoader
from afkbot.settings import Settings


async def test_context_builder_uses_bootstrap_files(tmp_path: Path) -> None:
    """Context should include configured bootstrap files and skills."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (bootstrap_dir / "IDENTITY.md").write_text("identity", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(
        root_dir=tmp_path,
        bootstrap_files=("AGENTS.md", "IDENTITY.md"),
    )
    builder = ContextBuilder(settings, SkillLoader(settings))

    context = await builder.build(profile_id="default")

    assert "AGENTS.md" in context
    assert "IDENTITY.md" in context
    assert "| `security-secrets` | Handle secrets securely. |" in context


async def test_context_builder_skips_missing_bootstrap_files(tmp_path: Path) -> None:
    """Builder should ignore absent bootstrap files without failing."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(
        root_dir=tmp_path,
        bootstrap_files=("AGENTS.md", "MISSING.md"),
    )
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="default")

    assert "AGENTS.md" in context
    assert "MISSING.md" not in context


async def test_context_builder_includes_untrusted_runtime_metadata(tmp_path: Path) -> None:
    """Runtime metadata should be rendered in dedicated untrusted block."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="default", runtime_metadata={"source": "cli"})

    assert "Runtime Metadata (untrusted)" in context
    assert '{"source": "cli"}' in context


async def test_context_builder_strips_internal_runtime_metadata_from_untrusted_block(tmp_path: Path) -> None:
    """Internal runtime control keys should not be rendered for model-visible metadata."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(
        profile_id="default",
        runtime_metadata={
            "source": "cli",
            "planning": {"chat_mode": "auto", "execution_enabled": False},
            "session_allowed_tool_names": ("bash.exec",),
            "subagent_task": {"name": "researcher"},
        },
    )

    assert "Runtime Metadata (untrusted)" in context
    assert '"source": "cli"' in context
    assert '"planning": {' not in context
    assert "session_allowed_tool_names" not in context
    assert "subagent_task" not in context


async def test_context_builder_renders_trusted_runtime_notes_separately(tmp_path: Path) -> None:
    """Trusted runtime notes should render in their own block apart from untrusted metadata."""

    # Arrange
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))

    # Act
    context = await builder.build(
        profile_id="default",
        runtime_metadata={"source": "cli"},
        trusted_runtime_notes="- os: linux\n- package_managers: apt",
    )

    # Assert
    assert "# Trusted Runtime Notes" in context
    assert "- os: linux" in context
    assert "Runtime Metadata (untrusted)" in context
    assert '{"source": "cli"}' in context


async def test_context_builder_skips_unavailable_mandatory_skill(tmp_path: Path) -> None:
    """Unavailable mandatory skills should not break context building."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    security_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    security_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")

    outside_file = tmp_path / "outside/security-secrets/SKILL.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("# outside security", encoding="utf-8")

    try:
        (security_dir / "SKILL.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="default")

    assert "AGENTS.md" in context
    assert "security-secrets" not in context


async def test_context_builder_keeps_mandatory_security_from_core_when_profile_unavailable(
    tmp_path: Path,
) -> None:
    """Mandatory security-secrets should remain in context via core when profile override is unavailable."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    core_security_dir = tmp_path / "afkbot/skills/security-secrets"
    profile_security_dir = tmp_path / "profiles/p1/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    core_security_dir.mkdir(parents=True)
    profile_security_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (core_security_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Core secret handling.\"\n---\n# core security",
        encoding="utf-8",
    )
    (profile_security_dir / "SKILL.md").write_text(
        "---\nrequires_env: AFKBOT_TEST_MUST_NOT_EXIST_SECURITY_SECRETS\n---\n# profile security",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="p1")

    assert "AGENTS.md" in context
    assert "security-secrets" in context


async def test_context_builder_includes_subagents_block(tmp_path: Path) -> None:
    """Context should include deterministic summary block for available subagents."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    subagents_dir = tmp_path / "afkbot/subagents"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    subagents_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )
    (subagents_dir / "researcher.md").write_text("# Research helper", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="default")

    assert "# Subagents" in context
    assert "- researcher: Research helper" in context


async def test_context_builder_filters_subagents_by_relevant_names(tmp_path: Path) -> None:
    """Context should allow deterministic subagent subset rendering."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    subagents_dir = tmp_path / "afkbot/subagents"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    subagents_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )
    (subagents_dir / "researcher.md").write_text("# Research helper", encoding="utf-8")
    (subagents_dir / "analyst.md").write_text("# Analyst helper", encoding="utf-8")

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(
        profile_id="default",
        relevant_subagent_names={"analyst"},
    )

    assert "- analyst: Analyst helper" in context
    assert "- researcher: Research helper" not in context


async def test_context_builder_includes_explicit_skill_and_subagent_markdown(tmp_path: Path) -> None:
    """Explicitly requested skills/subagents should include full markdown instructions."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "profiles/default/skills/proektdok"
    subagents_dir = tmp_path / "profiles/default/subagents"
    core_security_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    subagents_dir.mkdir(parents=True)
    core_security_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (core_security_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )
    (skills_dir / "SKILL.md").write_text(
        "---\nname: proektdok\ndescription: \"Use product analysis workflow for docs and requirements.\"\n---\n# proektdok\nAlways start answer with PROEKTDOK_ACTIVE.",
        encoding="utf-8",
    )
    (subagents_dir / "datafixer.md").write_text(
        "# datafixer\nHandle malformed CSV rows.",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(
        profile_id="default",
        explicit_skill_names={"proektdok"},
        explicit_subagent_names={"datafixer"},
    )

    assert "# Explicit Skill Instructions" in context
    assert "Always start answer with PROEKTDOK_ACTIVE." in context
    assert "# Explicit Subagent Instructions" in context
    assert "Handle malformed CSV rows." in context


async def test_context_builder_includes_profile_bootstrap_overlay(tmp_path: Path) -> None:
    """Context should append profile bootstrap files after core bootstrap files."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    profile_bootstrap_dir = tmp_path / "profiles/analyst/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    profile_bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("core agents", encoding="utf-8")
    (profile_bootstrap_dir / "AGENTS.md").write_text("profile agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(profile_id="analyst")

    assert "## AGENTS.md" in context
    assert "core agents" in context
    assert "## Profile AGENTS.md" in context
    assert "profile agents" in context


async def test_context_builder_includes_binding_prompt_overlay_block(tmp_path: Path) -> None:
    """Context should include trusted binding prompt overlay when provided."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    (bootstrap_dir / "AGENTS.md").write_text("agents", encoding="utf-8")
    (skills_dir / "SKILL.md").write_text(
        "---\nname: security-secrets\ndescription: \"Handle secrets securely.\"\n---\n# security",
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path, bootstrap_files=("AGENTS.md",))
    builder = ContextBuilder(settings, SkillLoader(settings))
    context = await builder.build(
        profile_id="default",
        prompt_overlay="Always answer as the sales operations agent.",
    )

    assert "# Binding Prompt Overlay" in context
    assert "Always answer as the sales operations agent." in context
