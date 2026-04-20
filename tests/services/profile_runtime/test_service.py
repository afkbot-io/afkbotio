"""Tests for profile CRUD service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import (
    ProfileService,
    get_profile_service,
    reset_profile_services_async,
    run_profile_service_sync,
)
from afkbot.settings import Settings


@pytest.mark.asyncio
async def test_profile_service_creates_profile_with_config_and_policy(tmp_path: Path) -> None:
    """Profile service should persist DB row, runtime config, and initial policy view."""

    # Arrange
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)
    try:
        # Act
        profile = await service.create(
            profile_id="analyst",
            name="Analyst",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                llm_thinking_level="high",
                chat_planning_mode="on",
                enabled_tool_plugins=("debug_echo",),
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="strict",
            policy_capabilities=("files", "web"),
            policy_network_allowlist=("api.search.brave.com",),
        )

        # Assert
        assert profile.id == "analyst"
        assert profile.name == "Analyst"
        assert profile.has_runtime_config is True
        assert profile.effective_runtime.llm_provider == "openai"
        assert profile.effective_runtime.llm_model == "gpt-4o-mini"
        assert profile.effective_runtime.llm_thinking_level == "high"
        assert profile.effective_runtime.chat_planning_mode == "on"
        assert profile.runtime_config is not None
        assert profile.runtime_config.llm_thinking_level == "high"
        assert profile.runtime_config.chat_planning_mode == "on"
        assert profile.runtime_config.enabled_tool_plugins == ("debug_echo",)
        assert profile.profile_root == "profiles/analyst"
        assert profile.system_dir == "profiles/analyst/.system"
        assert profile.runtime_config_path == "profiles/analyst/.system/agent_config.json"
        assert profile.bootstrap_dir == "profiles/analyst/bootstrap"
        assert profile.skills_dir == "profiles/analyst/skills"
        assert profile.subagents_dir == "profiles/analyst/subagents"
        assert profile.policy.allowed_directories == (str((tmp_path / "profiles/analyst").resolve()),)
        assert (tmp_path / "profiles/analyst/.system").is_dir()
        assert (tmp_path / "profiles/analyst/bootstrap").is_dir()
        assert (tmp_path / "profiles/analyst/skills").is_dir()
        assert (tmp_path / "profiles/analyst/subagents").is_dir()
        assert (tmp_path / "profiles/analyst/bootstrap/AGENTS.md").read_text(encoding="utf-8") == (
            "No profile-specific role instructions. Follow the global bootstrap and the current user request.\n"
        )
        assert (tmp_path / "profiles/analyst/bootstrap/IDENTITY.md").read_text(encoding="utf-8") == (
            "No profile-specific identity instructions. Use the global identity defaults.\n"
        )
        assert (tmp_path / "profiles/analyst/bootstrap/TOOLS.md").read_text(encoding="utf-8") == (
            "No profile-specific tool instructions. Use the global tool defaults.\n"
        )
        assert (tmp_path / "profiles/analyst/bootstrap/SECURITY.md").read_text(encoding="utf-8") == (
            "No profile-specific security instructions. Use the global security defaults.\n"
        )
    finally:
        await service.shutdown()


def test_get_profile_service_does_not_cache_across_sync_event_loop_boundaries(tmp_path: Path) -> None:
    """Sync callers should receive fresh services instead of reusing one async engine across loops."""

    asyncio.run(reset_profile_services_async())
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)

    first = get_profile_service(settings)
    second = get_profile_service(settings)

    assert first is not second

    asyncio.run(first.shutdown())
    asyncio.run(second.shutdown())


def test_run_profile_service_sync_creates_and_reads_default_profile(tmp_path: Path) -> None:
    """Sync helper should isolate setup-style profile operations from event-loop reuse."""

    asyncio.run(reset_profile_services_async())
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)

    created = run_profile_service_sync(
        settings,
        lambda service: service.bootstrap_default(
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=False,
            policy_preset=None,
            policy_capabilities=(),
            policy_network_allowlist=(),
        ),
    )
    loaded = run_profile_service_sync(
        settings,
        lambda service: service.get(profile_id="default"),
    )

    assert created.id == "default"
    assert loaded.id == "default"


def test_run_profile_service_sync_keeps_existing_default_bootstrap_files(tmp_path: Path) -> None:
    """Default profile bootstrap should seed missing files but preserve existing custom content."""

    # Arrange
    asyncio.run(reset_profile_services_async())
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)

    # Act
    run_profile_service_sync(
        settings,
        lambda service: service.bootstrap_default(
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=False,
            policy_preset=None,
            policy_capabilities=(),
            policy_network_allowlist=(),
        ),
    )
    agents_path = tmp_path / "profiles/default/bootstrap/AGENTS.md"
    security_path = tmp_path / "profiles/default/bootstrap/SECURITY.md"
    agents_path.write_text("Custom agent rules.\n", encoding="utf-8")
    security_path.unlink()
    run_profile_service_sync(
        settings,
        lambda service: service.bootstrap_default(
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=False,
            policy_preset=None,
            policy_capabilities=(),
            policy_network_allowlist=(),
        ),
    )

    # Assert
    assert agents_path.read_text(encoding="utf-8") == "Custom agent rules.\n"
    assert security_path.read_text(encoding="utf-8") == (
        "No profile-specific security instructions. Use the global security defaults.\n"
    )


@pytest.mark.asyncio
async def test_profile_service_rolls_back_db_row_when_runtime_config_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile creation should remain atomic if runtime config persistence fails."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)

    def _fail_write(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise OSError("disk full")

    monkeypatch.setattr(service._runtime_configs, "write", _fail_write)

    try:
        with pytest.raises(OSError, match="disk full"):
            await service.create(
                profile_id="broken",
                name="Broken",
                runtime_config=ProfileRuntimeConfig(
                    llm_provider="openai",
                    llm_model="gpt-4o-mini",
                ),
                runtime_secrets=None,
                policy_enabled=False,
                policy_preset=None,
                policy_capabilities=(),
                policy_network_allowlist=(),
            )

        profiles = await service.list()
        assert profiles == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_profile_service_removes_bootstrap_files_after_partial_seed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profile creation should remove already-seeded bootstrap files when later seeding fails."""

    # Arrange
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)
    write_calls = 0

    def _flaky_atomic_text_write(path: Path, content: str, *, mode: int) -> None:
        nonlocal write_calls
        write_calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        if write_calls == 3:
            raise OSError("disk full during bootstrap")
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    monkeypatch.setattr(
        "afkbot.services.profile_runtime.service.atomic_text_write",
        _flaky_atomic_text_write,
    )

    # Act
    try:
        with pytest.raises(OSError, match="disk full during bootstrap"):
            await service.create(
                profile_id="broken-bootstrap",
                name="Broken Bootstrap",
                runtime_config=ProfileRuntimeConfig(
                    llm_provider="openai",
                    llm_model="gpt-4o-mini",
                ),
                runtime_secrets=None,
                policy_enabled=False,
                policy_preset=None,
                policy_capabilities=(),
                policy_network_allowlist=(),
            )

        # Assert
        bootstrap_dir = tmp_path / "profiles/broken-bootstrap/bootstrap"
        assert not (bootstrap_dir / "AGENTS.md").exists()
        assert not (bootstrap_dir / "IDENTITY.md").exists()
        assert not (tmp_path / "profiles/broken-bootstrap/.system/agent_config.json").exists()
        assert await service.list() == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_profile_service_get_is_read_only_for_existing_layout_gaps(tmp_path: Path) -> None:
    """Reading profile details should not mutate missing layout directories."""

    # Arrange
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)
    try:
        # Act
        await service.create(
            profile_id="legacy",
            name="Legacy",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=False,
            policy_preset=None,
            policy_capabilities=(),
            policy_network_allowlist=(),
        )

        for child in (tmp_path / "profiles/legacy/bootstrap").iterdir():
            child.unlink()
        for path in (
            tmp_path / "profiles/legacy/bootstrap",
            tmp_path / "profiles/legacy/skills",
            tmp_path / "profiles/legacy/subagents",
        ):
            path.rmdir()

        profile = await service.get(profile_id="legacy")

        # Assert
        assert profile.bootstrap_dir == "profiles/legacy/bootstrap"
        assert profile.skills_dir == "profiles/legacy/skills"
        assert profile.subagents_dir == "profiles/legacy/subagents"
        assert not (tmp_path / "profiles/legacy/bootstrap").exists()
        assert not (tmp_path / "profiles/legacy/skills").exists()
        assert not (tmp_path / "profiles/legacy/subagents").exists()
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_profile_service_update_rewrites_runtime_and_policy(tmp_path: Path) -> None:
    """Profile update should rewrite persisted runtime config and effective policy view."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)
    try:
        await service.create(
            profile_id="ops",
            name="Ops",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                llm_history_turns=8,
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.telegram.org",),
        )

        updated = await service.update(
            profile_id="ops",
            name="Ops Updated",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4.1-mini",
                llm_history_turns=16,
                memory_auto_search_enabled=True,
                session_compaction_enabled=True,
            ),
            policy_enabled=True,
            policy_preset="strict",
            policy_capabilities=("memory",),
            policy_file_access_mode="none",
            policy_network_allowlist=(),
        )

        assert updated.name == "Ops Updated"
        assert updated.runtime_config is not None
        assert updated.runtime_config.llm_model == "gpt-4.1-mini"
        assert updated.effective_runtime.llm_history_turns == 16
        assert updated.effective_runtime.memory_auto_search_enabled is True
        assert updated.effective_runtime.session_compaction_enabled is True
        assert updated.policy.preset == "strict"
        assert updated.policy.file_access_mode == "none"
        assert updated.policy.capabilities == ("memory",)
        assert updated.policy.network_allowlist == ()
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_profile_service_delete_purges_profile_row_and_folder(tmp_path: Path) -> None:
    """Deleting profile should remove DB row and profile workspace tree."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}", root_dir=tmp_path)
    service = ProfileService(settings)
    try:
        await service.create(
            profile_id="support",
            name="Support",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.telegram.org",),
        )
        assert (tmp_path / "profiles/support").exists() is True

        deleted = await service.delete(profile_id="support")

        assert deleted.id == "support"
        assert (tmp_path / "profiles/support").exists() is False
        with pytest.raises(Exception):
            await service.get(profile_id="support")
    finally:
        await service.shutdown()
