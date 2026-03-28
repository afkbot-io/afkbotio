"""Tests for chat input catalog collection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from afkbot.services.chat_session.input_catalog import (
    _collect_profile_file_paths,
    build_chat_input_catalog,
)


@dataclass(frozen=True, slots=True)
class _NamedRecord:
    name: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class _ServerRecord:
    server: str
    enabled: bool = True


class _FakeSkillService:
    async def list(self, *, profile_id: str, scope: str, include_unavailable: bool) -> list[_NamedRecord]:
        _ = profile_id, scope, include_unavailable
        return [
            _NamedRecord(name="security-secrets", available=True),
            _NamedRecord(name="broken-skill", available=False),
        ]


class _BrokenSkillService:
    async def list(self, *, profile_id: str, scope: str, include_unavailable: bool) -> list[_NamedRecord]:
        _ = profile_id, scope, include_unavailable
        raise RuntimeError("skills offline")


class _FakeSubagentService:
    async def list(self, *, profile_id: str) -> list[_NamedRecord]:
        _ = profile_id
        return [_NamedRecord(name="reviewer")]


class _FakeAppRegistry:
    def list(self) -> tuple[_NamedRecord, ...]:
        return (_NamedRecord(name="imap"), _NamedRecord(name="telegram"))


class _FakeMCPProfileLoader:
    def __init__(self, _settings: object) -> None:
        pass

    def load_profile(self, _profile_id: str) -> tuple[_ServerRecord, ...]:
        return (
            _ServerRecord(server="alpha", enabled=True),
            _ServerRecord(server="beta", enabled=False),
        )


class _BrokenMCPProfileLoader:
    def __init__(self, _settings: object) -> None:
        pass

    def load_profile(self, _profile_id: str) -> tuple[_ServerRecord, ...]:
        raise ValueError("invalid mcp config")


class _FakeRuntimeConfigService:
    def __init__(self, profile_root: Path) -> None:
        self._profile_root = profile_root

    def profile_root(self, _profile_id: str) -> Path:
        return self._profile_root


class _FakeRuntimeMCPCatalog:
    def __init__(self) -> None:
        self.scheduled_profiles: list[str] = []

    def list_cached_tools(self, *, profile_id: str) -> tuple[object, ...]:
        _ = profile_id
        return (
            type("_Descriptor", (), {"runtime_name": "mcp.alpha.search"})(),
        )

    def schedule_refresh(self, *, profile_id: str, timeout_sec: int | None = None) -> None:
        _ = timeout_sec
        self.scheduled_profiles.append(profile_id)


class _LazyRuntimeMCPCatalog:
    def __init__(self) -> None:
        self.scheduled_profiles: list[str] = []

    def list_cached_tools(self, *, profile_id: str) -> tuple[object, ...]:
        _ = profile_id
        return ()

    def schedule_refresh(self, *, profile_id: str, timeout_sec: int | None = None) -> None:
        _ = timeout_sec
        self.scheduled_profiles.append(profile_id)


async def test_build_chat_input_catalog_collects_profile_capability_hints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catalog collection should include skills, subagents, apps, enabled MCP servers, and files."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    (profile_root / "notes").mkdir(parents=True)
    (profile_root / "notes/task.md").write_text("task", encoding="utf-8")
    runtime_catalog = _FakeRuntimeMCPCatalog()
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_skill_service",
        lambda _settings: _FakeSkillService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_subagent_service",
        lambda _settings: _FakeSubagentService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_app_registry",
        lambda **_: _FakeAppRegistry(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.MCPProfileLoader",
        _FakeMCPProfileLoader,
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_runtime_config_service",
        lambda _settings: _FakeRuntimeConfigService(profile_root),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_mcp_runtime_catalog",
        lambda _settings: runtime_catalog,
    )

    # Act
    catalog = await build_chat_input_catalog(settings=object(), profile_id="default")  # type: ignore[arg-type]

    # Assert
    assert catalog.skill_names == ("security-secrets",)
    assert catalog.subagent_names == ("reviewer",)
    assert catalog.app_names == ("imap", "telegram")
    assert catalog.mcp_server_names == ("alpha",)
    assert catalog.mcp_tool_names == ("mcp.alpha.search",)
    assert catalog.file_paths == ("notes/task.md",)
    assert runtime_catalog.scheduled_profiles == ["default"]


async def test_build_chat_input_catalog_keeps_other_sources_when_one_source_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """One broken catalog source should not blank the full chat completion surface."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    (profile_root / "notes").mkdir(parents=True)
    (profile_root / "notes/task.md").write_text("task", encoding="utf-8")
    runtime_catalog = _FakeRuntimeMCPCatalog()
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_skill_service",
        lambda _settings: _BrokenSkillService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_subagent_service",
        lambda _settings: _FakeSubagentService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_app_registry",
        lambda **_: _FakeAppRegistry(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.MCPProfileLoader",
        _BrokenMCPProfileLoader,
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_runtime_config_service",
        lambda _settings: _FakeRuntimeConfigService(profile_root),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_mcp_runtime_catalog",
        lambda _settings: runtime_catalog,
    )

    # Act
    catalog = await build_chat_input_catalog(settings=object(), profile_id="default")  # type: ignore[arg-type]

    # Assert
    assert catalog.skill_names == ()
    assert catalog.subagent_names == ("reviewer",)
    assert catalog.app_names == ("imap", "telegram")
    assert catalog.mcp_server_names == ()
    assert catalog.mcp_tool_names == ("mcp.alpha.search",)
    assert catalog.file_paths == ("notes/task.md",)
    assert runtime_catalog.scheduled_profiles == ["default"]


async def test_build_chat_input_catalog_uses_cached_mcp_tools_without_live_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catalog collection should stay non-blocking by using cached MCP tools plus refresh scheduling."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    (profile_root / "notes").mkdir(parents=True)
    (profile_root / "notes/task.md").write_text("task", encoding="utf-8")
    runtime_catalog = _LazyRuntimeMCPCatalog()
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_skill_service",
        lambda _settings: _FakeSkillService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_subagent_service",
        lambda _settings: _FakeSubagentService(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_app_registry",
        lambda **_: _FakeAppRegistry(),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.MCPProfileLoader",
        _FakeMCPProfileLoader,
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_profile_runtime_config_service",
        lambda _settings: _FakeRuntimeConfigService(profile_root),
    )
    monkeypatch.setattr(
        "afkbot.services.chat_session.input_catalog.get_mcp_runtime_catalog",
        lambda _settings: runtime_catalog,
    )

    # Act
    catalog = await build_chat_input_catalog(settings=object(), profile_id="default")  # type: ignore[arg-type]

    # Assert
    assert catalog.mcp_tool_names == ()
    assert runtime_catalog.scheduled_profiles == ["default"]


def test_collect_profile_file_paths_does_not_descend_into_skipped_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """File completion scans should prune skipped subtrees before recursing into them."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    hidden_directory = profile_root / ".git"
    visible_directory = profile_root / "notes"
    hidden_directory.mkdir(parents=True)
    visible_directory.mkdir(parents=True)
    (hidden_directory / "config").write_text("secret", encoding="utf-8")
    (visible_directory / "task.md").write_text("task", encoding="utf-8")
    original_iterdir = Path.iterdir

    def _guarded_iterdir(path: Path) -> Iterator[Path]:
        if path == hidden_directory:
            raise AssertionError("hidden directory should not be traversed")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", _guarded_iterdir)

    # Act
    file_paths = _collect_profile_file_paths(profile_root)

    # Assert
    assert file_paths == ("notes/task.md",)


def test_collect_profile_file_paths_skips_symlinks_outside_profile_scope(
    tmp_path: Path,
) -> None:
    """File completion scans should ignore symlinks that resolve outside the profile root."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    visible_directory = profile_root / "notes"
    visible_directory.mkdir(parents=True)
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (visible_directory / "task.md").write_text("task", encoding="utf-8")
    (outside_directory / "secrets.md").write_text("secret", encoding="utf-8")
    (profile_root / "outside-file.md").symlink_to(outside_directory / "secrets.md")
    (profile_root / "outside-dir").symlink_to(outside_directory, target_is_directory=True)

    # Act
    file_paths = _collect_profile_file_paths(profile_root)

    # Assert
    assert file_paths == ("notes/task.md",)


def test_collect_profile_file_paths_skips_symlinked_directories_inside_profile(
    tmp_path: Path,
) -> None:
    """File completion scans should skip symlinked directories to avoid completion loops."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    visible_directory = profile_root / "notes"
    visible_directory.mkdir(parents=True)
    (visible_directory / "task.md").write_text("task", encoding="utf-8")
    (profile_root / "notes-link").symlink_to(visible_directory, target_is_directory=True)

    # Act
    file_paths = _collect_profile_file_paths(profile_root)

    # Assert
    assert file_paths == ("notes/task.md",)
