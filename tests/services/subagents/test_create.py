"""Tests for profile-scoped subagent markdown CRUD."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.policy import ProfileFilesLock, ProfileFilesLockedError
from afkbot.services.subagents.profile_service import ProfileSubagentService
from afkbot.settings import Settings


async def test_profile_subagent_upsert_writes_profile_markdown(tmp_path: Path) -> None:
    """Profile service should write markdown under profile subagents path."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSubagentService(settings=settings)
    record = await service.upsert(
        profile_id="p1",
        name="research-helper",
        content="# helper\nprompt",
    )

    assert record.name == "research-helper"
    assert record.path == "profiles/p1/subagents/research-helper.md"
    assert record.content == "# helper\nprompt"
    assert (tmp_path / record.path).read_text(encoding="utf-8").startswith("# helper")


async def test_profile_subagent_upsert_replaces_existing_content(tmp_path: Path) -> None:
    """Upsert should replace existing profile markdown deterministically."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSubagentService(settings=settings)

    await service.upsert(profile_id="p1", name="x", content="# first")
    updated = await service.upsert(profile_id="p1", name="x", content="# second")

    assert updated.content == "# second"
    assert (tmp_path / updated.path).read_text(encoding="utf-8") == "# second"


async def test_profile_subagent_upsert_rejects_when_profile_files_locked(tmp_path: Path) -> None:
    """Concurrent profile file mutation should return deterministic lock error."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSubagentService(settings=settings)
    lock = ProfileFilesLock(root_dir=tmp_path)
    service._profile_files_lock = lock

    async with lock.acquire("p1"):
        with pytest.raises(ProfileFilesLockedError, match="profile: p1"):
            await service.upsert(profile_id="p1", name="locked", content="# locked")
