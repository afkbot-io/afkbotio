"""Operator-facing MCP orchestration for CLI management flows."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.mcp_integration.operator_contracts import (
    MCPAddPreview,
    MCPAddResult,
    MCPRemovePreview,
    MCPRemoveResult,
    MCPServerAccessView,
    MCPServerView,
    MCPValidationReport,
)
from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.mcp_integration.profile_store import MCPProfileStore, MCPStorageMode
from afkbot.services.mcp_integration.url_resolver import build_server_config_from_url
from afkbot.services.mcp_integration.validator import MCPConfigValidationError
from afkbot.services.mcp_runtime.catalog import runtime_available_for_server
from afkbot.services.policy import get_profile_files_lock
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime.service import get_profile_service
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "MCPProfileService"] = {}
_BOUNDARY_NOTE = (
    "Runtime MCP access uses `mcp.tools.list` / `mcp.tools.call` for enabled remote "
    "servers with `tools` capability and matching policy/network access."
)


class MCPProfileService:
    """Manage profile-scoped MCP IDE integration configs for operators."""

    def __init__(
        self,
        settings: Settings,
        *,
        loader: MCPProfileLoader | None = None,
        store: MCPProfileStore | None = None,
    ) -> None:
        self._settings = settings
        self._loader = loader or MCPProfileLoader(settings)
        self._store = store or MCPProfileStore(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)

    async def list(
        self,
        *,
        profile_id: str,
        show_disabled: bool = True,
    ) -> list[MCPServerView]:
        """Return merged effective MCP config for one profile."""

        normalized_profile_id = await self._ensure_profile_exists(profile_id)
        inspection = self._loader.inspect_profile(normalized_profile_id)
        views = [
            self._build_server_view(
                config=item.config,
                source=self._relative_path(item.source),
            )
            for item in inspection.servers
            if show_disabled or item.config.enabled
        ]
        return views

    async def get(
        self,
        *,
        profile_id: str,
        server: str,
    ) -> MCPServerView:
        """Return one effective MCP server view by identifier."""

        normalized_profile_id = await self._ensure_profile_exists(profile_id)
        normalized_server = self._normalize_server(server)
        inspection = self._loader.inspect_profile(normalized_profile_id)
        for item in inspection.servers:
            if item.config.server != normalized_server:
                continue
            return self._build_server_view(
                config=item.config,
                source=self._relative_path(item.source),
            )
        raise MCPIntegrationError(f"MCP server not found: {normalized_server}")

    async def validate(self, *, profile_id: str) -> MCPValidationReport:
        """Validate MCP files for one profile and return a structured report."""

        normalized_profile_id = await self._ensure_profile_exists(profile_id)
        try:
            inspection = self._loader.inspect_profile(normalized_profile_id)
            storage_mode = self._safe_storage_mode(normalized_profile_id)
        except (MCPConfigValidationError, MCPIntegrationError, ValueError) as exc:
            storage_mode = self._safe_storage_mode(normalized_profile_id)
            files_checked = tuple(
                self._relative_path(path)
                for path in self._safe_profile_paths(normalized_profile_id)
            )
            return MCPValidationReport(
                ok=False,
                profile_id=normalized_profile_id,
                storage_mode=storage_mode,
                files_checked=files_checked,
                notes=(_BOUNDARY_NOTE,),
                errors=(str(exc),),
            )

        return MCPValidationReport(
            ok=True,
            profile_id=normalized_profile_id,
            storage_mode=storage_mode,
            files_checked=tuple(self._relative_path(path) for path in inspection.files_checked),
            servers=tuple(
                self._build_server_view(
                    config=item.config,
                    source=self._relative_path(item.source),
                )
                for item in inspection.servers
            ),
            notes=(_BOUNDARY_NOTE,),
        )

    async def preview_add_by_url(
        self,
        *,
        profile_id: str,
        url: str,
        server: str,
        transport: str,
        capabilities: tuple[str, ...],
        env_refs: tuple[str, ...],
        secret_refs: tuple[str, ...],
        enabled: bool,
    ) -> MCPAddPreview:
        """Build a preview for one pending operator-managed remote MCP config."""

        normalized_profile_id = await self._ensure_profile_exists(profile_id)
        config = self._build_remote_server_config(
            url=url,
            server=server,
            transport=transport,
            capabilities=capabilities,
            env_refs=env_refs,
            secret_refs=secret_refs,
            enabled=enabled,
        )
        storage_mode = self._safe_storage_mode(normalized_profile_id)
        target_path = self._store.target_path(normalized_profile_id, storage_mode=storage_mode)
        existing_servers = {item.server for item in self._loader.load_profile(normalized_profile_id)}
        return MCPAddPreview(
            profile_id=normalized_profile_id,
            storage_mode=storage_mode,
            target_path=self._relative_path(target_path),
            would_replace_effective_server=config.server in existing_servers,
            server=self._build_server_view(
                config=config,
                source=self._relative_path(target_path),
            ),
        )

    async def add_by_url(
        self,
        *,
        profile_id: str,
        url: str,
        server: str,
        transport: str,
        capabilities: tuple[str, ...],
        env_refs: tuple[str, ...],
        secret_refs: tuple[str, ...],
        enabled: bool,
    ) -> MCPAddResult:
        """Persist one operator-managed remote MCP config."""

        preview = await self.preview_add_by_url(
            profile_id=profile_id,
            url=url,
            server=server,
            transport=transport,
            capabilities=capabilities,
            env_refs=env_refs,
            secret_refs=secret_refs,
            enabled=enabled,
        )
        normalized_profile_id = preview.profile_id
        config = self._build_remote_server_config(
            url=url,
            server=server,
            transport=transport,
            capabilities=capabilities,
            env_refs=env_refs,
            secret_refs=secret_refs,
            enabled=enabled,
        )
        storage_mode = self._safe_storage_mode(normalized_profile_id)

        async with self._profile_files_lock.acquire(normalized_profile_id):
            effective_before = {item.server for item in self._loader.load_profile(normalized_profile_id)}
            current = self._operator_entries_for_storage(
                profile_id=normalized_profile_id,
                storage_mode=storage_mode,
            )
            current[config.server] = config
            written_path = self._store.write_entries(
                profile_id=normalized_profile_id,
                storage_mode=storage_mode,
                entries=tuple(current.values()),
            )

        return MCPAddResult(
            profile_id=normalized_profile_id,
            storage_mode=storage_mode,
            target_path=self._relative_path(written_path),
            created=config.server not in effective_before,
            server=self._build_server_view(config=config, source=self._relative_path(written_path)),
        )

    async def preview_remove(
        self,
        *,
        profile_id: str,
        server: str,
    ) -> MCPRemovePreview:
        """Build a preview for one pending operator-managed MCP removal."""

        normalized_profile_id = await self._ensure_profile_exists(profile_id)
        normalized_server = self._normalize_server(server)
        inspection = self._loader.inspect_profile(normalized_profile_id)
        effective_servers = {item.config.server: item for item in inspection.servers}
        server_source = effective_servers.get(normalized_server)
        if server_source is None:
            raise MCPIntegrationError(f"MCP server not found: {normalized_server}")
        storage_mode = self._safe_storage_mode(normalized_profile_id)
        target_path = self._store.target_path(normalized_profile_id, storage_mode=storage_mode)
        if storage_mode == "multifile" and server_source.source.resolve() != target_path.resolve():
            raise MCPIntegrationError(
                "MCP server is not managed by AFKBOT CLI and cannot be removed here: "
                f"{normalized_server} (source={self._relative_path(server_source.source)})"
            )
        return MCPRemovePreview(
            profile_id=normalized_profile_id,
            storage_mode=storage_mode,
            target_path=self._relative_path(target_path),
            server=self._build_server_view(
                config=server_source.config,
                source=self._relative_path(server_source.source),
            ),
        )

    async def remove(
        self,
        *,
        profile_id: str,
        server: str,
    ) -> MCPRemoveResult:
        """Remove one operator-managed MCP server config entry."""

        preview = await self.preview_remove(profile_id=profile_id, server=server)
        async with self._profile_files_lock.acquire(preview.profile_id):
            current = self._operator_entries_for_storage(
                profile_id=preview.profile_id,
                storage_mode=preview.storage_mode,
            )
            if preview.server.server not in current:
                raise MCPIntegrationError(
                    f"MCP server not found in operator-managed config: {preview.server.server}"
                )
            current.pop(preview.server.server, None)
            written_path = self._store.write_entries(
                profile_id=preview.profile_id,
                storage_mode=preview.storage_mode,
                entries=tuple(current.values()),
            )

        return MCPRemoveResult(
            profile_id=preview.profile_id,
            storage_mode=preview.storage_mode,
            target_path=self._relative_path(written_path),
            removed_server=preview.server.server,
        )

    async def _ensure_profile_exists(self, profile_id: str) -> str:
        normalized_profile_id: str = validate_profile_id(profile_id)
        await get_profile_service(self._settings).get(profile_id=normalized_profile_id)
        return normalized_profile_id

    def _safe_storage_mode(self, profile_id: str) -> MCPStorageMode:
        """Return storage mode or fall back when the on-disk MCP shape is itself invalid."""

        try:
            return self._store.storage_mode(profile_id)
        except (MCPIntegrationError, ValueError):
            return "fallback"

    def _operator_entries_for_storage(
        self,
        *,
        profile_id: str,
        storage_mode: MCPStorageMode,
    ) -> dict[str, MCPServerConfig]:
        """Return operator-owned MCP entries for the selected storage mode."""

        if storage_mode == "fallback":
            items = self._loader.load_profile(profile_id)
        else:
            items = self._store.load_managed_entries(profile_id)
        return {item.server: item for item in items}

    @staticmethod
    def _build_remote_server_config(
        *,
        url: str,
        server: str,
        transport: str,
        capabilities: tuple[str, ...],
        env_refs: tuple[str, ...],
        secret_refs: tuple[str, ...],
        enabled: bool,
    ) -> MCPServerConfig:
        """Build one validated remote MCP server config from operator input."""

        return build_server_config_from_url(
            url=url,
            server=server,
            transport=transport,
            capabilities=capabilities,
            env_refs=env_refs,
            secret_refs=secret_refs,
            enabled=enabled,
        )

    def _build_server_view(
        self,
        *,
        config: MCPServerConfig,
        source: str | None,
    ) -> MCPServerView:
        runtime_available = runtime_available_for_server(
            settings=self._settings,
            config=config,
        )
        return MCPServerView(
            server=config.server,
            transport=config.transport,
            url=config.url,
            enabled=config.enabled,
            capabilities=config.capabilities,
            env_refs=tuple(item.env_ref for item in config.env_refs),
            secret_refs=tuple(item.secret_ref for item in config.secret_refs),
            config_source=source,
            access=MCPServerAccessView(
                capabilities=config.capabilities,
                env_refs=tuple(item.env_ref for item in config.env_refs),
                secret_refs=tuple(item.secret_ref for item in config.secret_refs),
                ide_visible=config.enabled,
                runtime_available=runtime_available,
                boundary_note=_BOUNDARY_NOTE,
            ),
        )

    @staticmethod
    def _normalize_server(server: str) -> str:
        normalized_server = server.strip().lower()
        if not normalized_server:
            raise ValueError("MCP server id cannot be empty")
        return normalized_server

    def _relative_path(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())

    def _safe_profile_paths(self, profile_id: str) -> tuple[Path, ...]:
        try:
            paths: tuple[Path, ...] = self._loader.list_profile_paths(profile_id)
            return paths
        except (MCPIntegrationError, ValueError):
            return ()


def get_mcp_profile_service(settings: Settings) -> MCPProfileService:
    """Return cached MCP profile service for one repository root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = MCPProfileService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_mcp_profile_services() -> None:
    """Reset cached MCP profile services for tests."""

    _SERVICES_BY_ROOT.clear()
