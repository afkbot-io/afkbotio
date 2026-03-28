"""Filesystem-backed write helpers for profile-scoped MCP config files."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.errors import MCPStorageError
from afkbot.services.mcp_integration.ide_adapter import serialize_server_for_ide
from afkbot.services.mcp_integration.path_scope import (
    ensure_path_in_scope,
    safe_profile_child,
    safe_profile_root,
)
from afkbot.services.mcp_integration.payloads import extract_server_payload_items, read_json_payload
from afkbot.services.mcp_integration.validator import validate_server_config
from afkbot.settings import Settings

MCPStorageMode = Literal["fallback", "multifile"]
_MANAGED_MULTIFILE_NAME = "zzz-afkbot-managed.json"


class MCPProfileStore:
    """Persist MCP configs while preserving fallback vs multi-file loader semantics."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def storage_mode(self, profile_id: str) -> MCPStorageMode:
        """Return the storage mode that should be used for operator writes."""

        return "multifile" if self.list_multifile_paths(profile_id) else "fallback"

    def list_multifile_paths(self, profile_id: str) -> tuple[Path, ...]:
        """Return profile-local `mcp/*.json` files in deterministic order."""

        mcp_dir = self.mcp_dir(profile_id)
        if not mcp_dir.exists():
            return ()
        if not mcp_dir.is_dir():
            raise MCPStorageError(f"Invalid profile mcp path (not a directory): {mcp_dir}")
        return tuple(
            ensure_path_in_scope(path, scope_root=mcp_dir)
            for path in sorted(mcp_dir.glob("*.json"), key=lambda item: item.name)
        )

    def fallback_path(self, profile_id: str) -> Path:
        """Return the fallback `mcp.json` file path for one profile."""

        return safe_profile_child(self._profile_root(profile_id), "mcp.json")

    def mcp_dir(self, profile_id: str) -> Path:
        """Return the multi-file MCP directory for one profile."""

        return safe_profile_child(self._profile_root(profile_id), "mcp")

    def managed_multifile_path(self, profile_id: str) -> Path:
        """Return the CLI-managed multi-file override path."""

        return safe_profile_child(self._profile_root(profile_id), "mcp", _MANAGED_MULTIFILE_NAME)

    def _profile_root(self, profile_id: str) -> Path:
        """Return one typed profile root path from the runtime-config service."""

        return safe_profile_root(self._settings, profile_id)

    def target_path(self, profile_id: str, *, storage_mode: MCPStorageMode) -> Path:
        """Return the config file that operator-managed writes will update."""

        return (
            self.managed_multifile_path(profile_id)
            if storage_mode == "multifile"
            else self.fallback_path(profile_id)
        )

    def load_managed_entries(self, profile_id: str) -> tuple[MCPServerConfig, ...]:
        """Load only the CLI-managed multi-file payload when it exists."""

        path = self.managed_multifile_path(profile_id)
        if not path.exists():
            return ()
        payload = read_json_payload(path)
        items = extract_server_payload_items(payload, source=path)
        return tuple(validate_server_config(item, source=path) for item in items)

    def write_entries(
        self,
        *,
        profile_id: str,
        storage_mode: MCPStorageMode,
        entries: tuple[MCPServerConfig, ...],
    ) -> Path:
        """Persist the provided MCP entries to the operator-owned target file."""

        target_path = self.target_path(profile_id, storage_mode=storage_mode)
        try:
            if storage_mode == "multifile":
                target_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "servers": [
                    serialize_server_for_ide(item)
                    for item in sorted(entries, key=lambda item: item.server)
                ]
            }
            atomic_json_write(target_path, payload, mode=0o600)
        except OSError as exc:
            raise MCPStorageError(f"Unable to write MCP config {target_path}: {exc}") from exc
        return target_path
