"""Profile-scoped runtime MCP tool discovery with bounded caching."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import re
import time
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from mcp import types as mcp_types

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.mcp_runtime.client import normalize_mcp_schema, open_mcp_client_session
from afkbot.services.mcp_runtime.contracts import MCPRuntimeToolDescriptor
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_RUNTIME_CACHE: dict[tuple[str, str], "_CachedCatalogEntry"] = {}
_REFRESH_TASKS: dict[tuple[str, str], asyncio.Task[tuple[MCPRuntimeToolDescriptor, ...]]] = {}
_RUNTIME_CACHE_LOCK = Lock()
_RUNTIME_NAME_SEGMENT_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class _CatalogFingerprint:
    profile_id: str
    file_tokens: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True, slots=True)
class _CachedCatalogEntry:
    fingerprint: _CatalogFingerprint
    loaded_at_monotonic: float
    descriptors: tuple[MCPRuntimeToolDescriptor, ...]


class MCPRuntimeCatalog:
    """Discover remote MCP tools for one profile and cache sanitized descriptors."""

    def __init__(
        self,
        *,
        settings: Settings,
        loader: MCPProfileLoader | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._loader = loader or MCPProfileLoader(settings)
        self._monotonic = monotonic or time.monotonic

    async def list_tools(
        self,
        *,
        profile_id: str,
        timeout_sec: int | None = None,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        """Return sanitized runtime MCP tools for one profile."""

        if not self._settings.mcp_runtime_enabled:
            return ()
        inspection = self._loader.inspect_profile(profile_id)
        fingerprint = _build_fingerprint(profile_id=profile_id, files_checked=inspection.files_checked)
        cached = self._get_cached(profile_id=profile_id, fingerprint=fingerprint)
        if cached is not None:
            return cached

        descriptors: list[MCPRuntimeToolDescriptor] = []
        for source in inspection.servers:
            config = source.config
            if not _is_runtime_enabled_server(config):
                continue
            try:
                server_tools = await self._list_server_tools(
                    config=config,
                    timeout_sec=_resolve_runtime_timeout(
                        settings=self._settings,
                        timeout_sec=timeout_sec,
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive boundary logging
                _LOGGER.warning(
                    "runtime MCP catalog skipped server %s for profile %s: %s",
                    config.server,
                    profile_id,
                    exc,
                )
                continue
            descriptors.extend(server_tools)

        stable_descriptors = tuple(sorted(descriptors, key=lambda item: item.runtime_name))
        self._store_cached(
            profile_id=profile_id,
            fingerprint=fingerprint,
            descriptors=stable_descriptors,
        )
        return stable_descriptors

    def list_tools_sync(
        self,
        *,
        profile_id: str,
        timeout_sec: int | None = None,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        """Return runtime MCP tools from sync-only entrypoints."""

        return _run_async_in_thread(self.list_tools(profile_id=profile_id, timeout_sec=timeout_sec))

    def list_cached_tools(self, *, profile_id: str) -> tuple[MCPRuntimeToolDescriptor, ...]:
        """Return the current runtime MCP catalog snapshot without network discovery."""

        if not self._settings.mcp_runtime_enabled:
            return ()
        inspection = self._loader.inspect_profile(profile_id)
        fingerprint = _build_fingerprint(profile_id=profile_id, files_checked=inspection.files_checked)
        cached = self._get_cached(profile_id=profile_id, fingerprint=fingerprint)
        return () if cached is None else cached

    def schedule_refresh(
        self,
        *,
        profile_id: str,
        timeout_sec: int | None = None,
    ) -> None:
        """Refresh runtime MCP discovery in the background when no fresh cache exists."""

        if not self._settings.mcp_runtime_enabled:
            return
        if self.list_cached_tools(profile_id=profile_id):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        cache_key = (str(self._settings.root_dir), profile_id)
        with _RUNTIME_CACHE_LOCK:
            existing_task = _REFRESH_TASKS.get(cache_key)
            if existing_task is not None and not existing_task.done():
                return
            task = loop.create_task(self.list_tools(profile_id=profile_id, timeout_sec=timeout_sec))
            _REFRESH_TASKS[cache_key] = task
        task.add_done_callback(lambda completed: _finalize_refresh_task(cache_key, completed))

    async def _list_server_tools(
        self,
        *,
        config: MCPServerConfig,
        timeout_sec: int,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        """Discover remote MCP tools for one server config."""

        async with open_mcp_client_session(config=config, timeout_sec=timeout_sec) as session:
            result = await session.list_tools()
        return _sanitize_runtime_tools(config=config, tools=result.tools)

    def _get_cached(
        self,
        *,
        profile_id: str,
        fingerprint: _CatalogFingerprint,
    ) -> tuple[MCPRuntimeToolDescriptor, ...] | None:
        cache_key = (str(self._settings.root_dir), profile_id)
        ttl_sec = max(1, self._settings.mcp_runtime_catalog_ttl_sec)
        with _RUNTIME_CACHE_LOCK:
            cached = _RUNTIME_CACHE.get(cache_key)
            if cached is None:
                return None
            if cached.fingerprint != fingerprint:
                return None
            if self._monotonic() - cached.loaded_at_monotonic > float(ttl_sec):
                return None
            return cached.descriptors

    def _store_cached(
        self,
        *,
        profile_id: str,
        fingerprint: _CatalogFingerprint,
        descriptors: tuple[MCPRuntimeToolDescriptor, ...],
    ) -> None:
        cache_key = (str(self._settings.root_dir), profile_id)
        with _RUNTIME_CACHE_LOCK:
            _RUNTIME_CACHE[cache_key] = _CachedCatalogEntry(
                fingerprint=fingerprint,
                loaded_at_monotonic=self._monotonic(),
                descriptors=descriptors,
            )


def _build_fingerprint(
    *,
    profile_id: str,
    files_checked: tuple[Path, ...],
) -> _CatalogFingerprint:
    tokens: list[tuple[str, int, int]] = []
    for path in files_checked:
        stat_result = path.stat()
        tokens.append((str(path), int(stat_result.st_mtime_ns), int(stat_result.st_size)))
    return _CatalogFingerprint(profile_id=profile_id, file_tokens=tuple(tokens))


def _is_runtime_enabled_server(config: MCPServerConfig) -> bool:
    return bool(
        config.enabled
        and config.url
        and config.transport in {"http", "sse", "websocket"}
        and "tools" in config.capabilities
    )


def _sanitize_runtime_tools(
    *,
    config: MCPServerConfig,
    tools: Sequence[mcp_types.Tool],
) -> tuple[MCPRuntimeToolDescriptor, ...]:
    descriptors: list[MCPRuntimeToolDescriptor] = []
    used_names: set[str] = set()
    for tool in tools:
        runtime_name = _build_runtime_tool_name(
            server_name=config.server,
            remote_tool_name=str(tool.name),
            used_names=used_names,
        )
        if runtime_name is None:
            continue
        input_schema = normalize_mcp_schema(tool.inputSchema)
        description = str(tool.description or tool.title or tool.name).strip() or str(tool.name)
        descriptors.append(
            MCPRuntimeToolDescriptor(
                runtime_name=runtime_name,
                server_name=config.server,
                remote_tool_name=str(tool.name),
                transport=config.transport,
                url=str(config.url),
                description=description,
                input_schema=input_schema,
            )
        )
    return tuple(descriptors)


def _build_runtime_tool_name(
    *,
    server_name: str,
    remote_tool_name: str,
    used_names: set[str],
) -> str | None:
    normalized_server = _normalize_runtime_name_segment(server_name)
    normalized_tool = _normalize_runtime_name_segment(remote_tool_name)
    if not normalized_server or not normalized_tool:
        return None
    candidate = f"mcp.{normalized_server}.{normalized_tool}"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    index = 2
    while True:
        next_candidate = f"{candidate}__{index}"
        if next_candidate not in used_names:
            used_names.add(next_candidate)
            return next_candidate
        index += 1


def _normalize_runtime_name_segment(raw: str) -> str:
    lowered = raw.strip().lower().replace(" ", "_")
    lowered = _RUNTIME_NAME_SEGMENT_RE.sub("_", lowered)
    normalized = lowered.strip("._-")
    return normalized


def _resolve_runtime_timeout(
    *,
    settings: Settings,
    timeout_sec: int | None,
) -> int:
    if timeout_sec is None:
        requested_timeout = settings.mcp_runtime_discovery_timeout_sec
    else:
        requested_timeout = timeout_sec
    return min(
        settings.tool_timeout_max_sec,
        max(1, int(requested_timeout)),
    )


def _finalize_refresh_task(
    cache_key: tuple[str, str],
    task: asyncio.Task[tuple[MCPRuntimeToolDescriptor, ...]],
) -> None:
    with _RUNTIME_CACHE_LOCK:
        _REFRESH_TASKS.pop(cache_key, None)
    try:
        task.result()
    except Exception as exc:  # pragma: no cover - defensive boundary logging
        _LOGGER.warning("runtime MCP background refresh failed for %s: %s", cache_key[1], exc)


def _run_async_in_thread(coro: Coroutine[Any, Any, tuple[MCPRuntimeToolDescriptor, ...]]) -> tuple[MCPRuntimeToolDescriptor, ...]:
    """Run async catalog discovery from sync-only runtime entrypoints."""

    future: Future[tuple[MCPRuntimeToolDescriptor, ...]] = Future()

    def _runner() -> None:
        try:
            future.set_result(asyncio.run(coro))
        except Exception as exc:  # pragma: no cover - defensive boundary
            future.set_exception(exc)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="afkbot-mcp-runtime") as executor:
        executor.submit(_runner)
        return future.result()


_CATALOGS_BY_ROOT: dict[str, MCPRuntimeCatalog] = {}


def get_mcp_runtime_catalog(settings: Settings) -> MCPRuntimeCatalog:
    """Return cached runtime MCP catalog for one repository root."""

    cache_key = str(settings.root_dir)
    with _RUNTIME_CACHE_LOCK:
        catalog = _CATALOGS_BY_ROOT.get(cache_key)
        if catalog is None:
            catalog = MCPRuntimeCatalog(settings=settings)
            _CATALOGS_BY_ROOT[cache_key] = catalog
        return catalog


def reset_mcp_runtime_catalogs() -> None:
    """Reset cached runtime MCP catalogs for tests."""

    with _RUNTIME_CACHE_LOCK:
        _CATALOGS_BY_ROOT.clear()
        _RUNTIME_CACHE.clear()


def runtime_available_for_server(
    *,
    settings: Settings,
    config: MCPServerConfig,
) -> bool:
    """Return whether one MCP server config is eligible for runtime tool exposure."""

    if not settings.mcp_runtime_enabled:
        return False
    if not _is_runtime_enabled_server(config):
        return False
    parsed = urlparse(str(config.url))
    return bool(parsed.hostname)
