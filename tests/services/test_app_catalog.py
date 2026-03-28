"""Tests for app-facing runtime catalog service."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from afkbot.services.app_catalog import AppCatalogService, get_app_catalog_service
from afkbot.services.skills.skills import SkillInfo, SkillManifest
from afkbot.services.subagents.contracts import SubagentInfo
from afkbot.settings import Settings


async def test_app_catalog_service_builds_runtime_and_mentions(monkeypatch, tmp_path: Path) -> None:
    """Catalog service should normalize runtime metadata and mention entries for the app."""

    settings = Settings(root_dir=tmp_path)
    service = AppCatalogService(settings)

    subagent_path = tmp_path / "afkbot/subagents/researcher.md"
    subagent_path.parent.mkdir(parents=True)
    subagent_path.write_text(
        "# Researcher\n\nCollect factual context from local files and contracts.",
        encoding="utf-8",
    )

    async def _fake_get(*, profile_id: str):
        assert profile_id == "default"
        return SimpleNamespace(
            id="default",
            name="Default",
            effective_runtime=SimpleNamespace(
                llm_provider="openai",
                llm_model="gpt-5",
                llm_thinking_level="high",
                chat_planning_mode="auto",
            ),
            policy=SimpleNamespace(
                preset="medium",
                file_access_mode="read_write",
                capabilities=("files", "shell"),
            ),
        )

    async def _fake_list_skills(profile_id: str):
        assert profile_id == "default"
        return [
            SkillInfo(
                name="browser-control",
                path=tmp_path / "afkbot/skills/browser-control/SKILL.md",
                origin="core",
                available=True,
                missing_requirements=(),
                missing_suggested_requirements=(),
                summary="Real browser automation.",
                aliases=("browser",),
                manifest=SkillManifest(
                    name="browser-control",
                    description="Real browser automation.",
                ),
            ),
            SkillInfo(
                name="tg-helper",
                path=tmp_path / "profiles/default/skills/tg-helper/SKILL.md",
                origin="profile",
                available=False,
                missing_requirements=("telegram",),
                missing_suggested_requirements=(),
                summary="Profile-local Telegram helper.",
                aliases=("tg",),
                manifest=SkillManifest(
                    name="tg-helper",
                    description="Profile-local Telegram helper.",
                ),
            ),
        ]

    async def _fake_list_subagents(profile_id: str):
        assert profile_id == "default"
        return [SubagentInfo(name="researcher", path=subagent_path, origin="core")]

    monkeypatch.setattr(
        "afkbot.services.app_catalog.get_profile_service",
        lambda settings: SimpleNamespace(get=_fake_get),
    )
    service._skills = SimpleNamespace(list_skills=_fake_list_skills)  # type: ignore[assignment]
    service._subagents = SimpleNamespace(list_subagents=_fake_list_subagents)  # type: ignore[assignment]

    catalog = await service.get_catalog(profile_id="default", session_id="desktop-session")

    assert catalog.profile_id == "default"
    assert catalog.profile_name == "Default"
    assert catalog.session_id == "desktop-session"
    assert catalog.runtime.model_dump() == {
        "llm_provider": "openai",
        "llm_model": "gpt-5",
        "thinking_level": "high",
        "planning_mode": "auto",
        "policy_preset": "medium",
        "file_access_mode": "read_write",
        "capabilities": ("files", "shell"),
    }
    assert [item.slug for item in catalog.mentions] == [
        "browser-control",
        "researcher",
        "tg-helper",
    ]
    assert catalog.mentions[0].model_dump() == {
        "kind": "skill",
        "slug": "browser-control",
        "title": "Browser Control",
        "description": "Real browser automation.",
        "aliases": ("browser",),
        "origin": "core",
        "available": True,
    }
    assert catalog.mentions[1].kind == "subagent"
    assert catalog.mentions[1].description == "Collect factual context from local files and contracts."
    assert catalog.mentions[2].origin == "profile"
    assert catalog.mentions[2].available is False


def test_get_app_catalog_service_caches_by_root(tmp_path: Path) -> None:
    """Catalog service cache should remain stable for one workspace root."""

    first = get_app_catalog_service(Settings(root_dir=tmp_path))
    second = get_app_catalog_service(Settings(root_dir=tmp_path))
    third = get_app_catalog_service(Settings(root_dir=tmp_path / "other"))

    assert first is second
    assert first is not third


def test_get_app_catalog_service_refreshes_when_settings_change(tmp_path: Path) -> None:
    """Catalog service cache should refresh when a root reuses different settings values."""

    first = get_app_catalog_service(Settings(root_dir=tmp_path, llm_model="gpt-4o-mini"))
    second = get_app_catalog_service(Settings(root_dir=tmp_path, llm_model="gpt-5"))

    assert first is not second
    assert first._settings.llm_model == "gpt-4o-mini"
    assert second._settings.llm_model == "gpt-5"
