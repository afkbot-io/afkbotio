"""Tests for profile skill/subagent management tool plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pytest import MonkeyPatch

from afkbot.services.skills import (
    SkillMarketplaceError,
    SkillMarketplaceInstallRecord,
    SkillMarketplaceListItem,
    SkillMarketplaceListResult,
    SkillMarketplaceSourceStats,
)
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


def _prepare(tmp_path: Path, monkeypatch: MonkeyPatch) -> tuple[Settings, ToolRegistry]:
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    settings = get_settings()
    return settings, ToolRegistry.from_settings(settings)


async def test_skill_profile_tools_crud(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """skill.profile.* tools should create, list, read, and delete profile skills."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    upsert_tool = registry.get("skill.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "custom-note",
            "markdown": "# custom-note\n\nUse me.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True
    upsert_skill = cast(dict[str, Any], upsert_result.payload["skill"])
    assert upsert_skill["name"] == "custom-note"
    assert upsert_skill["summary"] == "Use me."

    list_tool = registry.get("skill.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    list_skills = cast(list[dict[str, Any]], list_result.payload["skills"])
    names = {str(item["name"]) for item in list_skills}
    assert "custom-note" in names

    get_tool = registry.get("skill.profile.get")
    assert get_tool is not None
    get_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "custom-note",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    get_result = await get_tool.execute(ctx, get_params)
    assert get_result.ok is True
    get_skill = cast(dict[str, Any], get_result.payload["skill"])
    assert str(get_skill["content"]).startswith("---\nname: custom-note\ndescription: \"Use me.\"")
    assert "Use me." in str(get_skill["content"])

    delete_tool = registry.get("skill.profile.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "name": "custom-note",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True


async def test_skill_profile_list_includes_core_and_profile_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.profile.list should return all visible skills by default."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    core_skill_dir = tmp_path / "afkbot/skills/security-secrets"
    core_skill_dir.mkdir(parents=True, exist_ok=True)
    (core_skill_dir / "SKILL.md").write_text("# core security", encoding="utf-8")

    upsert_tool = registry.get("skill.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "custom-note",
            "markdown": "# custom-note\n\nUse me.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True

    list_tool = registry.get("skill.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    names = {str(item["name"]) for item in cast(list[dict[str, Any]], list_result.payload["skills"])}
    assert "security-secrets" in names
    assert "custom-note" in names


async def test_skill_profile_list_scope_profile_filters_core(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.profile.list scope=profile should only return profile-local skills."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    core_skill_dir = tmp_path / "afkbot/skills/security-secrets"
    core_skill_dir.mkdir(parents=True, exist_ok=True)
    (core_skill_dir / "SKILL.md").write_text("# core security", encoding="utf-8")

    upsert_tool = registry.get("skill.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "custom-note",
            "markdown": "# custom-note\n\nUse me.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True

    list_tool = registry.get("skill.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "scope": "profile",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    names = {str(item["name"]) for item in cast(list[dict[str, Any]], list_result.payload["skills"])}
    assert names == {"custom-note"}
    assert list_result.payload["scope"] == "profile"
    assert list_result.payload["core_skill_count"] == 1
    assert "profile-local skills" in str(list_result.payload["scope_note"])
    assert "Skills in scope `profile`:" in str(list_result.payload["display_text"])
    assert "`custom-note`" in str(list_result.payload["display_text"])


async def test_skill_profile_list_scope_profile_reports_core_hint_when_empty(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """scope=profile should explain when only core skills exist."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    core_skill_dir = tmp_path / "afkbot/skills/telegram"
    core_skill_dir.mkdir(parents=True, exist_ok=True)
    (core_skill_dir / "SKILL.md").write_text("# telegram\nBuilt-in telegram skill.", encoding="utf-8")

    list_tool = registry.get("skill.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "scope": "profile",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)

    assert list_result.ok is True
    assert list_result.payload["skills"] == []
    assert list_result.payload["core_skill_count"] == 1
    assert "Core skills are available" in str(list_result.payload["hint"])
    assert list_result.payload["display_text"] == "No skills found for scope `profile`."


async def test_skill_profile_get_supports_scope_all_and_core(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.profile.get should read skills from selected scope."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    core_skill_dir = tmp_path / "afkbot/skills/demo-core"
    core_skill_dir.mkdir(parents=True, exist_ok=True)
    (core_skill_dir / "SKILL.md").write_text("# demo-core\nCore only.", encoding="utf-8")

    get_tool = registry.get("skill.profile.get")
    assert get_tool is not None

    core_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "demo-core",
            "scope": "core",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    core_result = await get_tool.execute(ctx, core_params)
    assert core_result.ok is True
    core_skill = cast(dict[str, Any], core_result.payload["skill"])
    assert "Core only." in str(core_skill["content"])

    all_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "demo-core",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    all_result = await get_tool.execute(ctx, all_params)
    assert all_result.ok is True
    all_skill = cast(dict[str, Any], all_result.payload["skill"])
    assert "Core only." in str(all_skill["content"])


async def test_subagent_profile_tools_crud(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """subagent.profile.* tools should create, list, read, and delete profile subagents."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    upsert_tool = registry.get("subagent.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "analyst",
            "markdown": "# analyst\n\nFocus on evidence.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True
    upsert_subagent = cast(dict[str, Any], upsert_result.payload["subagent"])
    assert upsert_subagent["name"] == "analyst"

    list_tool = registry.get("subagent.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    list_subagents = cast(list[dict[str, Any]], list_result.payload["subagents"])
    names = {str(item["name"]) for item in list_subagents}
    assert "analyst" in names

    get_tool = registry.get("subagent.profile.get")
    assert get_tool is not None
    get_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "analyst",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    get_result = await get_tool.execute(ctx, get_params)
    assert get_result.ok is True
    get_subagent = cast(dict[str, Any], get_result.payload["subagent"])
    assert "Focus on evidence." in str(get_subagent["content"])

    delete_tool = registry.get("subagent.profile.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "name": "analyst",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True


async def test_profile_asset_tools_reject_profile_mismatch(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile-scoped CRUD tools should reject mismatched profile routing."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("skill.profile.list")
    assert tool is not None
    params = tool.parse_params(
        {"profile_key": "other"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is False
    assert result.error_code == "profile_not_found"

    marketplace_tool = registry.get("skill.marketplace.list")
    assert marketplace_tool is not None
    marketplace_params = marketplace_tool.parse_params(
        {
            "profile_key": "other",
            "source": "skills.sh/openai/skills",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    marketplace_result = await marketplace_tool.execute(ctx, marketplace_params)
    assert marketplace_result.ok is False
    assert marketplace_result.error_code == "profile_not_found"


async def test_skill_profile_tools_normalize_localized_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.profile.* should normalize localized names into runtime-safe slugs."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    upsert_tool = registry.get("skill.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "Продуктолог",
            "markdown": "# Продуктолог\n\nСодержимое.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True
    upsert_skill = cast(dict[str, Any], upsert_result.payload["skill"])
    assert upsert_skill["name"] == "produktolog"

    get_tool = registry.get("skill.profile.get")
    assert get_tool is not None
    get_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "продуктолог",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    get_result = await get_tool.execute(ctx, get_params)
    assert get_result.ok is True
    get_skill = cast(dict[str, Any], get_result.payload["skill"])
    assert get_skill["name"] == "produktolog"

    delete_tool = registry.get("skill.profile.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "name": "ПРОДУКТОЛОГ",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True
    delete_skill = cast(dict[str, Any], delete_result.payload["skill"])
    assert delete_skill["name"] == "produktolog"


async def test_subagent_profile_tools_normalize_localized_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """subagent.profile.* should normalize localized names into runtime-safe slugs."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    upsert_tool = registry.get("subagent.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "name": "Анализатор",
            "markdown": "# Анализатор\n\nФокус на фактах.",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True
    upsert_subagent = cast(dict[str, Any], upsert_result.payload["subagent"])
    assert upsert_subagent["name"] == "analizator"

    get_tool = registry.get("subagent.profile.get")
    assert get_tool is not None
    get_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "name": "анализатор",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    get_result = await get_tool.execute(ctx, get_params)
    assert get_result.ok is True
    get_subagent = cast(dict[str, Any], get_result.payload["subagent"])
    assert get_subagent["name"] == "analizator"

    delete_tool = registry.get("subagent.profile.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "name": "АНАЛИЗАТОР",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True
    delete_subagent = cast(dict[str, Any], delete_result.payload["subagent"])
    assert delete_subagent["name"] == "analizator"


async def test_profile_tools_reject_unrecoverable_localized_names(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Unrecoverable names should still return invalid_* error codes."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    skill_upsert = registry.get("skill.profile.upsert")
    assert skill_upsert is not None
    skill_params = skill_upsert.parse_params(
        {
            "profile_key": "default",
            "name": "___",
            "markdown": "# x",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    skill_result = await skill_upsert.execute(ctx, skill_params)
    assert skill_result.ok is False
    assert skill_result.error_code == "invalid_skill_name"

    subagent_upsert = registry.get("subagent.profile.upsert")
    assert subagent_upsert is not None
    subagent_params = subagent_upsert.parse_params(
        {
            "profile_key": "default",
            "name": "___",
            "markdown": "# x",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    subagent_result = await subagent_upsert.execute(ctx, subagent_params)
    assert subagent_result.ok is False
    assert subagent_result.error_code == "invalid_subagent_name"


async def test_skill_marketplace_tools_list_and_install(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.marketplace.* should list and install profile-local skills."""

    # Arrange
    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    class _MarketplaceService:
        async def list_source(
            self,
            *,
            source: str,
            limit: int | None = None,
            profile_id: str | None = None,
        ) -> SkillMarketplaceListResult:
            assert source == "skills.sh/openai/skills"
            assert limit == 50
            assert profile_id == "default"
            return SkillMarketplaceListResult(
                source=source,
                source_stats=SkillMarketplaceSourceStats(
                    installs_source="skills.sh",
                    total_installs=16_400,
                    total_installs_display="16.4K",
                    repo_social_source="github",
                    repo_stars=14_572,
                    repo_forks=841,
                    repo_watchers=85,
                ),
                items=(
                    SkillMarketplaceListItem(
                        name="memory",
                        source=source,
                        path="openai/skills/main/skills/memory/SKILL.md",
                        canonical_source="https://raw.githubusercontent.com/openai/skills/main/skills/memory/SKILL.md",
                        rank=2,
                        installs=362,
                        installs_display="362",
                        installed=True,
                        installed_name="memory-local",
                        installed_origin="profile",
                    ),
                ),
            )

        async def install(
            self,
            *,
            profile_id: str,
            source: str,
            skill: str | None = None,
            target_name: str | None = None,
            overwrite: bool = False,
        ) -> SkillMarketplaceInstallRecord:
            assert profile_id == "default"
            assert source == "skills.sh/openai/skills"
            assert skill == "memory"
            assert target_name == "memory-local"
            assert overwrite is False
            return SkillMarketplaceInstallRecord(
                name="memory-local",
                path="profiles/default/skills/memory-local/SKILL.md",
                source="https://raw.githubusercontent.com/openai/skills/main/skills/memory/SKILL.md",
                summary="Memory skill",
            )

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.skill_marketplace.plugin.get_skill_marketplace_service",
        lambda _: _MarketplaceService(),
    )

    # Act
    list_tool = registry.get("skill.marketplace.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "source": "skills.sh/openai/skills",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)

    install_tool = registry.get("skill.marketplace.install")
    assert install_tool is not None
    install_params = install_tool.parse_params(
        {
            "profile_key": "default",
            "source": "skills.sh/openai/skills",
            "skill": "memory",
            "target_name": "memory-local",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    install_result = await install_tool.execute(ctx, install_params)

    # Assert
    assert list_result.ok is True
    assert list_result.payload["resolved_source"] == "skills.sh/openai/skills"
    assert cast(dict[str, Any], list_result.payload["source_stats"])["total_installs"] == 16_400
    skills = cast(list[dict[str, Any]], list_result.payload["skills"])
    assert skills == [
        {
            "name": "memory",
            "source": "skills.sh/openai/skills",
            "path": "openai/skills/main/skills/memory/SKILL.md",
            "summary": "",
            "canonical_source": "https://raw.githubusercontent.com/openai/skills/main/skills/memory/SKILL.md",
            "rank": 2,
            "installs": 362,
            "installs_display": "362",
            "installed": True,
            "installed_name": "memory-local",
            "installed_origin": "profile",
        }
    ]
    assert "Marketplace skills in `skills.sh/openai/skills`:" in str(list_result.payload["display_text"])
    assert "ranking: 16.4K total installs" in str(list_result.payload["display_text"])
    assert "`memory` | #2 | 362 installs | installed as `memory-local`" in str(
        list_result.payload["display_text"]
    )
    assert install_result.ok is True
    installed = cast(dict[str, Any], install_result.payload["skill"])
    assert installed["name"] == "memory-local"
    assert installed["summary"] == "Memory skill"


async def test_skill_marketplace_tools_default_source_alias(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Marketplace tools should treat default source alias as the curated source."""

    # Arrange
    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    class _MarketplaceService:
        async def list_source(
            self,
            *,
            source: str,
            limit: int | None = None,
            profile_id: str | None = None,
        ) -> SkillMarketplaceListResult:
            assert source == "default"
            assert limit == 50
            assert profile_id == "default"
            return SkillMarketplaceListResult(
                source="skills.sh/openai/skills",
                items=(
                    SkillMarketplaceListItem(
                        name="memory",
                        source="skills.sh/openai/skills",
                        path="openai/skills/main/skills/memory/SKILL.md",
                        installed=True,
                        installed_name="memory",
                    ),
                ),
            )

        async def install(
            self,
            *,
            profile_id: str,
            source: str,
            skill: str | None = None,
            target_name: str | None = None,
            overwrite: bool = False,
        ) -> SkillMarketplaceInstallRecord:
            assert profile_id == "default"
            assert source == "default"
            assert skill == "memory"
            assert overwrite is False
            return SkillMarketplaceInstallRecord(
                name=target_name or "memory",
                path="profiles/default/skills/memory/SKILL.md",
                source="skills.sh/openai/skills",
                summary="Memory skill",
            )

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.skill_marketplace.plugin.get_skill_marketplace_service",
        lambda _: _MarketplaceService(),
    )

    # Act
    list_tool = registry.get("skill.marketplace.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)

    install_tool = registry.get("skill.marketplace.install")
    assert install_tool is not None
    install_params = install_tool.parse_params(
        {
            "profile_key": "default",
            "skill": "memory",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    install_result = await install_tool.execute(ctx, install_params)

    # Assert
    assert list_result.ok is True
    assert list_result.payload["resolved_source"] == "skills.sh/openai/skills"
    assert cast(list[dict[str, Any]], list_result.payload["skills"])[0]["source"] == "skills.sh/openai/skills"
    assert cast(list[dict[str, Any]], list_result.payload["skills"])[0]["installed"] is True
    assert "Marketplace skills in `default`:" in str(list_result.payload["display_text"])
    assert "installed" in str(list_result.payload["display_text"])
    assert install_result.ok is True
    installed = cast(dict[str, Any], install_result.payload["skill"])
    assert installed["source"] == "skills.sh/openai/skills"


async def test_skill_marketplace_tools_use_deterministic_error_codes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """skill.marketplace.* should return deterministic error codes for failures."""

    # Arrange
    settings, registry = _prepare(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    class _MarketplaceServiceWithErrors:
        async def list_source(
            self,
            *,
            source: str,
            limit: int | None = None,
            profile_id: str | None = None,
        ) -> SkillMarketplaceListResult:
            _ = profile_id
            assert limit == 50
            if source == "skills.sh/openai/skills":
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_invalid_source",
                    reason="Unsupported source",
                )
            raise RuntimeError("boom")

        async def install(
            self,
            *,
            profile_id: str,
            source: str,
            skill: str | None = None,
            target_name: str | None = None,
            overwrite: bool = False,
        ) -> SkillMarketplaceInstallRecord:
            _ = profile_id, skill, target_name, overwrite
            if source == "skills.sh/openai/skills":
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_already_exists",
                    reason="Skill already exists",
                )
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.skill_marketplace.plugin.get_skill_marketplace_service",
        lambda _: _MarketplaceServiceWithErrors(),
    )

    # Act
    list_tool = registry.get("skill.marketplace.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "source": "skills.sh/openai/skills",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is False
    assert list_result.error_code == "skill_marketplace_invalid_source"
    assert list_result.reason == "Unsupported source"

    list_fallback_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "source": "https://example.com/crash",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_fallback_result = await list_tool.execute(ctx, list_fallback_params)
    assert list_fallback_result.ok is False
    assert list_fallback_result.error_code == "skill_marketplace_list_failed"

    install_tool = registry.get("skill.marketplace.install")
    assert install_tool is not None
    install_params = install_tool.parse_params(
        {
            "profile_key": "default",
            "source": "skills.sh/openai/skills",
            "skill": "memory",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    install_result = await install_tool.execute(ctx, install_params)
    assert install_result.ok is False
    assert install_result.error_code == "skill_marketplace_already_exists"
    assert install_result.reason == "Skill already exists"

    install_fallback_params = install_tool.parse_params(
        {
            "profile_key": "default",
            "source": "https://example.com/crash",
            "skill": "memory",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    install_fallback_result = await install_tool.execute(ctx, install_fallback_params)

    # Assert
    assert install_fallback_result.ok is False
    assert install_fallback_result.error_code == "skill_marketplace_install_failed"
