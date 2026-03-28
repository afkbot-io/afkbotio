"""Profile-scoped MCP configuration loader."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.errors import MCPStorageError
from afkbot.services.mcp_integration.path_scope import (
    ensure_path_in_scope,
    safe_profile_child,
    safe_profile_root,
)
from afkbot.services.mcp_integration.payloads import extract_server_payload_items, read_json_payload
from afkbot.services.mcp_integration.validator import (
    validate_server_config,
)
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class MCPProfileServerSource:
    """One validated MCP server config and the file that contributed it."""

    config: MCPServerConfig
    source: Path


@dataclass(frozen=True, slots=True)
class MCPProfileInspection:
    """Merged MCP profile result with validated source metadata."""

    servers: tuple[MCPProfileServerSource, ...]
    files_checked: tuple[Path, ...]


class MCPProfileLoader:
    """Load merged MCP profile configuration from filesystem."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def load_profile(self, profile_id: str) -> tuple[MCPServerConfig, ...]:
        """Load profile MCP config with multi-file precedence over fallback file."""

        inspection = self.inspect_profile(profile_id)
        return tuple(item.config for item in inspection.servers)

    def load_profile_for_ide(self, profile_id: str) -> dict[str, object]:
        """Load profile config and serialize into deterministic IDE payload format."""

        from afkbot.services.mcp_integration.ide_adapter import serialize_profile_for_ide

        return serialize_profile_for_ide(self.load_profile(profile_id))

    def inspect_profile(self, profile_id: str) -> MCPProfileInspection:
        """Load profile MCP config together with source file metadata."""

        profile_root = safe_profile_root(self._settings, profile_id)
        mcp_root = safe_profile_child(profile_root, "mcp")
        paths = self.list_profile_paths(profile_id)
        if not paths:
            return MCPProfileInspection(servers=(), files_checked=())
        scope_root = mcp_root if paths[0].parent == mcp_root else profile_root
        return self._merge_files(paths, scope_root=scope_root)

    def list_profile_paths(self, profile_id: str) -> tuple[Path, ...]:
        """Return safe MCP config file paths using the loader scope rules."""

        profile_root = safe_profile_root(self._settings, profile_id)
        mcp_root = safe_profile_child(profile_root, "mcp")
        multifile_paths = self._list_multifile_configs(mcp_root)
        if multifile_paths:
            return multifile_paths
        fallback = safe_profile_child(profile_root, "mcp.json")
        if fallback.exists():
            return (ensure_path_in_scope(fallback, scope_root=profile_root),)
        return ()

    def _list_multifile_configs(self, mcp_root: Path) -> tuple[Path, ...]:
        if not mcp_root.exists():
            return ()
        if not mcp_root.is_dir():
            raise MCPStorageError(f"Invalid profile mcp path (not a directory): {mcp_root}")

        paths: list[Path] = []
        for path in sorted(mcp_root.glob("*.json"), key=lambda item: item.name):
            paths.append(ensure_path_in_scope(path, scope_root=mcp_root))
        return tuple(paths)

    def _merge_files(
        self,
        paths: Sequence[Path],
        *,
        scope_root: Path,
    ) -> MCPProfileInspection:
        merged: dict[str, MCPProfileServerSource] = {}
        files_checked: list[Path] = []
        for path in paths:
            safe_path = ensure_path_in_scope(path, scope_root=scope_root)
            files_checked.append(safe_path)
            payload = read_json_payload(safe_path)
            for item in extract_server_payload_items(payload, source=safe_path):
                config = validate_server_config(item, source=safe_path)
                merged[config.server] = MCPProfileServerSource(config=config, source=safe_path)
        return MCPProfileInspection(
            servers=tuple(merged[name] for name in sorted(merged.keys())),
            files_checked=tuple(files_checked),
        )
