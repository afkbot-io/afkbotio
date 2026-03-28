"""Tests for profile-scoped MCP loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.settings import Settings


def _server_payload(
    *,
    server: str,
    transport: str = "stdio",
    capabilities: list[str] | None = None,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "server": server,
        "transport": transport,
        "capabilities": capabilities or ["tools"],
        "env_refs": [{"env_ref": "MCP_ENV"}],
        "secret_refs": [{"secret_ref": f"{server}_secret"}],
        "enabled": enabled,
    }


def test_loader_prefers_multifile_and_merges_by_server_last_wins(tmp_path: Path) -> None:
    """`mcp/*.json` should override fallback and merge duplicate servers deterministically."""

    profile_root = tmp_path / "profiles/p1"
    mcp_dir = profile_root / "mcp"
    mcp_dir.mkdir(parents=True)

    (profile_root / "mcp.json").write_text(
        '[{"server":"fallback","transport":"stdio","capabilities":["tools"],'
        '"env_refs":[{"env_ref":"X"}],"secret_refs":[{"secret_ref":"fallback"}],"enabled":true}]',
        encoding="utf-8",
    )
    (mcp_dir / "01-base.json").write_text(
        (
            '[{"server":"alpha","transport":"stdio","capabilities":["tools"],'
            '"env_refs":[{"env_ref":"A"}],"secret_refs":[{"secret_ref":"alpha_one"}],"enabled":true}]'
        ),
        encoding="utf-8",
    )
    (mcp_dir / "02-override.json").write_text(
        (
            '[{"server":"alpha","transport":"http","capabilities":["resources"],'
            '"env_refs":[{"env_ref":"A2"}],"secret_refs":[{"secret_ref":"alpha_two"}],"enabled":false},'
            '{"server":"beta","transport":"stdio","capabilities":["tools"],'
            '"env_refs":[{"env_ref":"B"}],"secret_refs":[{"secret_ref":"beta"}],"enabled":true}]'
        ),
        encoding="utf-8",
    )

    loader = MCPProfileLoader(Settings(root_dir=tmp_path))
    loaded = loader.load_profile("p1")
    by_server = {item.server: item for item in loaded}

    assert set(by_server.keys()) == {"alpha", "beta"}
    assert by_server["alpha"].transport == "http"
    assert by_server["alpha"].enabled is False
    assert by_server["alpha"].secret_refs[0].secret_ref == "alpha_two"


def test_loader_uses_mcp_json_fallback_when_multifile_missing(tmp_path: Path) -> None:
    """Fallback `mcp.json` should be loaded only when no multi-file config exists."""

    profile_root = tmp_path / "profiles/p2"
    profile_root.mkdir(parents=True)
    (profile_root / "mcp.json").write_text(
        '[{"server":"fallback","transport":"stdio","capabilities":["tools"],'
        '"env_refs":[{"env_ref":"F"}],"secret_refs":[{"secret_ref":"fallback"}],"enabled":true}]',
        encoding="utf-8",
    )

    loader = MCPProfileLoader(Settings(root_dir=tmp_path))
    loaded = loader.load_profile("p2")

    assert len(loaded) == 1
    assert loaded[0].server == "fallback"


def test_loader_rejects_invalid_profile_id(tmp_path: Path) -> None:
    """Traversal-like profile IDs must be rejected."""

    loader = MCPProfileLoader(Settings(root_dir=tmp_path))
    with pytest.raises(ValueError, match="Invalid profile id"):
        loader.load_profile("../outside")


def test_loader_rejects_symlinked_config_outside_profile_scope(tmp_path: Path) -> None:
    """Symlinked JSON outside `profiles/<id>/mcp` must fail path-scope checks."""

    profile_root = tmp_path / "profiles/p3"
    mcp_dir = profile_root / "mcp"
    mcp_dir.mkdir(parents=True)

    outside = tmp_path / "outside.json"
    outside.write_text(
        (
            '[{"server":"outside","transport":"stdio","capabilities":["tools"],'
            '"env_refs":[{"env_ref":"OUT"}],"secret_refs":[{"secret_ref":"outside"}],"enabled":true}]'
        ),
        encoding="utf-8",
    )
    try:
        (mcp_dir / "01-link.json").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks are not supported in this environment")

    loader = MCPProfileLoader(Settings(root_dir=tmp_path))
    with pytest.raises(ValueError, match="outside profile scope"):
        loader.load_profile("p3")


def test_loader_serializes_for_ide_payload(tmp_path: Path) -> None:
    """IDE adapter payload should preserve validated server contracts."""

    profile_root = tmp_path / "profiles/p4"
    profile_root.mkdir(parents=True)
    payload = [_server_payload(server="gamma", capabilities=["tools", "resources"])]
    (profile_root / "mcp.json").write_text(json.dumps(payload), encoding="utf-8")

    loader = MCPProfileLoader(Settings(root_dir=tmp_path))
    ide_payload = loader.load_profile_for_ide("p4")

    assert list(ide_payload.keys()) == ["servers"]
    assert ide_payload["servers"] == [
        {
            "server": "gamma",
            "transport": "stdio",
            "capabilities": ["tools", "resources"],
            "env_refs": [{"env_ref": "MCP_ENV"}],
            "secret_refs": [{"secret_ref": "gamma_secret"}],
            "enabled": True,
        }
    ]
