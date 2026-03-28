"""Tests for operator-facing MCP profile service flows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.service import get_mcp_profile_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_service
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'mcp-service.db'}")
    get_settings.cache_clear()


async def _create_profile() -> None:
    settings = get_settings()
    await get_profile_service(settings).create(
        profile_id="default",
        name="Default",
        runtime_config=ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
        runtime_secrets=None,
        policy_enabled=True,
        policy_preset="medium",
        policy_capabilities=(),
        policy_network_allowlist=(),
    )


async def test_mcp_service_add_by_url_writes_fallback_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Remote add should write fallback `mcp.json` when multi-file config is absent."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    settings = get_settings()
    service = get_mcp_profile_service(settings)

    # Act
    result = await service.add_by_url(
        profile_id="default",
        url="https://example.com/mcp",
        server="example",
        transport="http",
        capabilities=("tools", "resources"),
        env_refs=("MCP_EXAMPLE_BASE_URL",),
        secret_refs=("mcp_example_token",),
        enabled=True,
    )
    report = await service.validate(profile_id="default")

    # Assert
    assert result.created is True
    assert result.storage_mode == "fallback"
    assert result.target_path == "profiles/default/mcp.json"
    assert result.server.url == "https://example.com/mcp"
    assert report.ok is True
    assert report.storage_mode == "fallback"
    assert report.files_checked == ("profiles/default/mcp.json",)
    assert report.servers[0].access.runtime_available is True
    written = json.loads((tmp_path / "profiles/default/mcp.json").read_text(encoding="utf-8"))
    assert written["servers"][0]["url"] == "https://example.com/mcp"


async def test_mcp_service_normalizes_server_ids_for_storage_and_lookup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Mixed-case server ids should be stored canonically and remain retrievable."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    service = get_mcp_profile_service(get_settings())

    # Act
    result = await service.add_by_url(
        profile_id="default",
        url="https://example.com/mcp",
        server="MyServer",
        transport="http",
        capabilities=("tools",),
        env_refs=(),
        secret_refs=("mcp_example_token",),
        enabled=True,
    )
    upper_lookup = await service.get(profile_id="default", server="MYSERVER")
    remove_result = await service.remove(profile_id="default", server="MyServer")

    # Assert
    assert result.server.server == "myserver"
    assert upper_lookup.server == "myserver"
    assert remove_result.removed_server == "myserver"


async def test_mcp_service_add_by_url_uses_managed_multifile_when_profile_already_has_mcp_dir(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Remote add should write the managed override file when multi-file MCP config already exists."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    existing_dir = tmp_path / "profiles/default/mcp"
    existing_dir.mkdir(parents=True)
    (existing_dir / "10-existing.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "existing",
                        "transport": "http",
                        "url": "https://existing.example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [{"secret_ref": "existing_token"}],
                        "enabled": True,
                    }
                ]
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    service = get_mcp_profile_service(settings)

    # Act
    result = await service.add_by_url(
        profile_id="default",
        url="wss://remote.example.com/ws",
        server="remote",
        transport="websocket",
        capabilities=("tools",),
        env_refs=(),
        secret_refs=("remote_token",),
        enabled=False,
    )
    items = await service.list(profile_id="default", show_disabled=True)

    # Assert
    assert result.created is True
    assert result.storage_mode == "multifile"
    assert result.target_path == "profiles/default/mcp/zzz-afkbot-managed.json"
    assert {item.server for item in items} == {"existing", "remote"}
    assert next(item for item in items if item.server == "remote").enabled is False


async def test_mcp_service_validate_rejects_symlinked_config_outside_profile_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Validation should reject multi-file MCP entries that resolve outside the profile scope."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "outside",
                        "transport": "http",
                        "url": "https://outside.example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [{"secret_ref": "outside_token"}],
                        "enabled": True,
                    }
                ]
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    mcp_dir = tmp_path / "profiles/default/mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "10-outside.json").symlink_to(outside)
    service = get_mcp_profile_service(get_settings())

    # Act
    report = await service.validate(profile_id="default")

    # Assert
    assert report.ok is False
    assert any("outside profile scope" in error for error in report.errors)


async def test_mcp_service_validate_reports_invalid_mcp_dir_shape_without_crashing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Validation should return a failed report when `profiles/<id>/mcp` is a file, not a directory."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    (tmp_path / "profiles/default/mcp").write_text("{}", encoding="utf-8")
    service = get_mcp_profile_service(get_settings())

    # Act
    report = await service.validate(profile_id="default")

    # Assert
    assert report.ok is False
    assert report.storage_mode == "fallback"
    assert any("not a directory" in error for error in report.errors)


async def test_mcp_service_add_rejects_symlinked_multifile_target_outside_profile_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Operator writes should reject `profiles/<id>/mcp` symlinks that escape the profile root."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    outside_directory = tmp_path / "outside-mcp"
    outside_directory.mkdir()
    managed_dir = tmp_path / "profiles/default/mcp"
    managed_dir.parent.mkdir(parents=True, exist_ok=True)
    managed_dir.symlink_to(outside_directory, target_is_directory=True)
    service = get_mcp_profile_service(get_settings())

    # Act
    with pytest.raises(MCPIntegrationError) as error_info:
        await service.add_by_url(
            profile_id="default",
            url="https://example.com/mcp",
            server="example",
            transport="http",
            capabilities=("tools",),
            env_refs=(),
            secret_refs=("mcp_example_token",),
            enabled=True,
        )

    # Assert
    assert "outside profile scope" in str(error_info.value)


async def test_mcp_service_add_rejects_symlinked_fallback_target_outside_profile_scope(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Fallback MCP writes should reject `mcp.json` symlinks that escape the profile root."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    outside_file = tmp_path / "outside-mcp.json"
    outside_file.write_text('{"servers": []}', encoding="utf-8")
    fallback_path = tmp_path / "profiles/default/mcp.json"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.symlink_to(outside_file)
    service = get_mcp_profile_service(get_settings())

    # Act
    with pytest.raises(MCPIntegrationError) as error_info:
        await service.add_by_url(
            profile_id="default",
            url="https://example.com/mcp",
            server="example",
            transport="http",
            capabilities=("tools",),
            env_refs=(),
            secret_refs=("mcp_example_token",),
            enabled=True,
        )

    # Assert
    assert "outside profile scope" in str(error_info.value)
    assert outside_file.read_text(encoding="utf-8") == '{"servers": []}'

async def test_mcp_service_remove_clears_fallback_server_entry(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Fallback-mode removal should drop the managed server from the profile file."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    settings = get_settings()
    service = get_mcp_profile_service(settings)
    await service.add_by_url(
        profile_id="default",
        url="https://example.com/mcp",
        server="example",
        transport="http",
        capabilities=("tools",),
        env_refs=(),
        secret_refs=("mcp_example_token",),
        enabled=True,
    )

    # Act
    current = await service.get(profile_id="default", server="example")
    result = await service.remove(profile_id="default", server="example")
    items = await service.list(profile_id="default", show_disabled=True)

    # Assert
    assert current.server == "example"
    assert current.url == "https://example.com/mcp"
    assert result.removed_server == "example"
    assert result.target_path == "profiles/default/mcp.json"
    assert items == []
    written = json.loads((tmp_path / "profiles/default/mcp.json").read_text(encoding="utf-8"))
    assert written["servers"] == []


async def test_mcp_service_remove_rejects_unmanaged_multifile_server(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Multi-file removal should not mutate servers owned by non-managed MCP files."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    await _create_profile()
    existing_dir = tmp_path / "profiles/default/mcp"
    existing_dir.mkdir(parents=True)
    (existing_dir / "10-existing.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "existing",
                        "transport": "http",
                        "url": "https://existing.example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [{"secret_ref": "existing_token"}],
                        "enabled": True,
                    }
                ]
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    service = get_mcp_profile_service(get_settings())

    # Act
    with pytest.raises(MCPIntegrationError) as error_info:
        await service.remove(profile_id="default", server="existing")

    # Assert
    assert "cannot be removed here" in str(error_info.value)
