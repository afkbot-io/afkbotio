"""Tests for subagent markdown loader."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from afkbot.services.subagents.loader import SubagentLoader, reset_subagent_loader_caches
from afkbot.settings import Settings


async def test_loader_lists_core_and_profile_subagents(tmp_path: Path) -> None:
    """Loader should merge core and profile subagents with profile override."""

    core_path = tmp_path / "afkbot/subagents/researcher.md"
    profile_path = tmp_path / "profiles/p1/subagents/researcher.md"
    extra_path = tmp_path / "profiles/p1/subagents/helper.md"
    core_path.parent.mkdir(parents=True)
    profile_path.parent.mkdir(parents=True)

    core_path.write_text("# core researcher", encoding="utf-8")
    profile_path.write_text("# profile researcher", encoding="utf-8")
    extra_path.write_text("# helper", encoding="utf-8")

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    items = await loader.list_subagents("p1")
    names = [item.name for item in items]

    assert "researcher" in names
    assert "helper" in names

    resolved = await loader.resolve_subagent("researcher", "p1")
    assert resolved.origin == "profile"
    assert resolved.path == profile_path.resolve()


async def test_loader_uses_default_researcher(tmp_path: Path) -> None:
    """Loader should resolve default subagent when name is omitted."""

    core_path = tmp_path / "afkbot/subagents/researcher.md"
    core_path.parent.mkdir(parents=True)
    core_path.write_text("# core researcher", encoding="utf-8")

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    info = await loader.resolve_subagent(None, "default")
    assert info.name == "researcher"


async def test_loader_rejects_invalid_profile_id(tmp_path: Path) -> None:
    """Traversal-like profile ids must be rejected."""

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    with pytest.raises(ValueError):
        await loader.list_subagents("../bad")


async def test_loader_skips_out_of_scope_symlink_on_list_and_rejects_resolve(
    tmp_path: Path,
) -> None:
    """Symlinked subagents outside scope must be hidden and fail explicit resolution."""

    subagents_root = tmp_path / "afkbot/subagents"
    subagents_root.mkdir(parents=True)

    outside_file = tmp_path / "outside/unsafe.md"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("# outside", encoding="utf-8")

    try:
        (subagents_root / "unsafe.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    items = await loader.list_subagents("default")
    names = {item.name for item in items}

    assert "unsafe" not in names
    with pytest.raises(ValueError):
        await loader.resolve_subagent("unsafe", "default")


async def test_loader_ignores_nested_entries_and_list_matches_resolve_contract(
    tmp_path: Path,
) -> None:
    """Discovery must be one-level and stay consistent with resolve()."""

    core_top = tmp_path / "afkbot/subagents/analyst.md"
    core_nested = tmp_path / "afkbot/subagents/team/helper.md"
    profile_top = tmp_path / "profiles/p1/subagents/analyst.md"
    profile_nested = tmp_path / "profiles/p1/subagents/group/ignored.md"

    core_top.parent.mkdir(parents=True)
    core_nested.parent.mkdir(parents=True)
    profile_top.parent.mkdir(parents=True)
    profile_nested.parent.mkdir(parents=True)

    core_top.write_text("# core analyst", encoding="utf-8")
    core_nested.write_text("# nested helper", encoding="utf-8")
    profile_top.write_text("# profile analyst", encoding="utf-8")
    profile_nested.write_text("# nested ignored", encoding="utf-8")

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    items = await loader.list_subagents("p1")
    item_map = {item.name: item for item in items}

    assert "analyst" in item_map
    assert item_map["analyst"].origin == "profile"
    assert "helper" not in item_map
    assert "ignored" not in item_map

    resolved = await loader.resolve_subagent("analyst", "p1")
    assert resolved.path == profile_top.resolve()

    with pytest.raises(FileNotFoundError):
        await loader.resolve_subagent("helper", "p1")


async def test_loader_invalidates_process_cache_after_subagent_update(tmp_path: Path) -> None:
    """Process-local discovery cache should refresh when subagent markdown changes."""

    reset_subagent_loader_caches()
    subagent_path = tmp_path / "afkbot/subagents/researcher.md"
    subagent_path.parent.mkdir(parents=True)
    subagent_path.write_text("# First researcher", encoding="utf-8")

    loader = SubagentLoader(Settings(root_dir=tmp_path))
    first = await loader.load_subagent_markdown("researcher", "default")
    assert "First researcher" in first

    time.sleep(0.01)
    subagent_path.write_text("# Updated researcher", encoding="utf-8")

    second = await loader.load_subagent_markdown("researcher", "default")
    assert "Updated researcher" in second
