"""Tests for MCP profile management tool plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pytest import MonkeyPatch

from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_service
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


def _prepare(tmp_path: Path, monkeypatch: MonkeyPatch) -> tuple[Settings, ToolRegistry]:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'mcp-tools.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    return settings, ToolRegistry.from_settings(settings)


async def _create_profile(settings: Settings) -> None:
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


async def test_mcp_profile_tools_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """mcp.profile.* tools should connect, inspect, validate, list, and delete one MCP server."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    await _create_profile(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    upsert_tool = registry.get("mcp.profile.upsert")
    assert upsert_tool is not None
    upsert_params = upsert_tool.parse_params(
        {
            "profile_key": "default",
            "url": "https://example.com/mcp",
            "secret_refs": ["mcp_example_token"],
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    upsert_result = await upsert_tool.execute(ctx, upsert_params)
    assert upsert_result.ok is True
    add_result = cast(dict[str, Any], upsert_result.payload["result"])
    assert add_result["server"]["server"] == "example"
    assert add_result["server"]["url"] == "https://example.com/mcp"
    validation = cast(dict[str, Any], upsert_result.payload["validation"])
    assert validation["ok"] is True

    get_tool = registry.get("mcp.profile.get")
    assert get_tool is not None
    get_params = get_tool.parse_params(
        {
            "profile_key": "default",
            "server": "example",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    get_result = await get_tool.execute(ctx, get_params)
    assert get_result.ok is True
    get_server = cast(dict[str, Any], get_result.payload["server"])
    assert get_server["secret_refs"] == ["mcp_example_token"]

    list_tool = registry.get("mcp.profile.list")
    assert list_tool is not None
    list_params = list_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    list_result = await list_tool.execute(ctx, list_params)
    assert list_result.ok is True
    servers = cast(list[dict[str, Any]], list_result.payload["servers"])
    assert [item["server"] for item in servers] == ["example"]

    validate_tool = registry.get("mcp.profile.validate")
    assert validate_tool is not None
    validate_params = validate_tool.parse_params(
        {"profile_key": "default"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    validate_result = await validate_tool.execute(ctx, validate_params)
    assert validate_result.ok is True
    report = cast(dict[str, Any], validate_result.payload["report"])
    assert report["ok"] is True

    delete_tool = registry.get("mcp.profile.delete")
    assert delete_tool is not None
    delete_params = delete_tool.parse_params(
        {
            "profile_key": "default",
            "server": "example",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    delete_result = await delete_tool.execute(ctx, delete_params)
    assert delete_result.ok is True
    removed = cast(dict[str, Any], delete_result.payload["result"])
    assert removed["removed_server"] == "example"


async def test_mcp_profile_tools_reject_profile_mismatch(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """mcp.profile.* tools should reject mismatched routed profiles."""

    settings, registry = _prepare(tmp_path, monkeypatch)
    await _create_profile(settings)
    ctx = ToolContext(profile_id="default", session_id="s", run_id=1)

    tool = registry.get("mcp.profile.list")
    assert tool is not None
    params = tool.parse_params(
        {"profile_key": "other"},
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ctx, params)
    assert result.ok is False
    assert result.error_code == "profile_not_found"
