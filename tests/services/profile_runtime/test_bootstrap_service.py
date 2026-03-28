"""Tests for profile bootstrap/system-prompt management."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.bootstrap_service import ProfileBootstrapService
from afkbot.services.profile_runtime.service import ProfileService, ProfileServiceError
from afkbot.settings import Settings


async def _create_profile(tmp_path: Path) -> Settings:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}",
        root_dir=tmp_path,
        bootstrap_files=("AGENTS.md", "IDENTITY.md"),
    )
    service = ProfileService(settings)
    try:
        await service.create(
            profile_id="analyst",
            name="Analyst",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=(),
        )
    finally:
        await service.shutdown()
    return settings


@pytest.mark.asyncio
async def test_profile_bootstrap_service_roundtrip(tmp_path: Path) -> None:
    """Bootstrap service should write, read, list, and remove overrides."""

    settings = await _create_profile(tmp_path)
    service = ProfileBootstrapService(settings)

    written = await service.write(
        profile_id="analyst",
        file_name="AGENTS.md",
        content="You are the analyst agent.",
    )
    listed = await service.list(profile_id="analyst")
    loaded = await service.get(profile_id="analyst", file_name="AGENTS.md")
    cleared = await service.remove(profile_id="analyst", file_name="AGENTS.md")

    assert written.exists is True
    assert written.content == "You are the analyst agent.\n"
    assert written.path == "profiles/analyst/bootstrap/AGENTS.md"
    assert [item.file_name for item in listed] == ["AGENTS.md", "IDENTITY.md"]
    assert listed[0].exists is True
    assert listed[1].exists is True
    assert loaded.content == "You are the analyst agent.\n"
    assert cleared.exists is False
    assert cleared.content is None


@pytest.mark.asyncio
async def test_profile_bootstrap_service_rejects_unsupported_file_name(tmp_path: Path) -> None:
    """Bootstrap service should enforce bootstrap file allowlist from settings."""

    settings = await _create_profile(tmp_path)
    service = ProfileBootstrapService(settings)

    with pytest.raises(ProfileServiceError, match="Unsupported bootstrap file"):
        await service.write(
            profile_id="analyst",
            file_name="MEMORY.md",
            content="not allowed",
        )
