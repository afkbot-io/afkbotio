"""Build static chat-input suggestion catalogs for one profile session."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from afkbot.services.apps.registry import get_app_registry
from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.mcp_runtime.catalog import get_mcp_runtime_catalog
from afkbot.services.profile_runtime.runtime_config import (
    get_profile_runtime_config_service,
)
from afkbot.services.skills.profile_service import get_profile_skill_service
from afkbot.services.subagents.profile_service import get_profile_subagent_service
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_FILE_SCAN_LIMIT = 500
_SKIP_DIRECTORY_NAMES = {".git", ".system", "__pycache__"}


@dataclass(frozen=True, slots=True)
class ChatInputCatalog:
    """Static suggestion catalog used by interactive chat prompt completion."""

    skill_names: tuple[str, ...]
    subagent_names: tuple[str, ...]
    app_names: tuple[str, ...] = ()
    mcp_server_names: tuple[str, ...] = ()
    mcp_tool_names: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()


class ChatInputCatalogStore:
    """Thread-safe mutable store for the current prompt completion catalog."""

    def __init__(self, catalog: ChatInputCatalog | None = None) -> None:
        self._catalog = catalog or ChatInputCatalog(
            skill_names=(),
            subagent_names=(),
            app_names=(),
            mcp_server_names=(),
            mcp_tool_names=(),
            file_paths=(),
        )
        self._lock = threading.Lock()

    def current(self) -> ChatInputCatalog:
        """Return the latest prompt completion catalog snapshot."""

        with self._lock:
            return self._catalog

    def replace(self, catalog: ChatInputCatalog) -> None:
        """Swap in a newly collected prompt completion catalog."""

        with self._lock:
            self._catalog = catalog


async def build_chat_input_catalog(
    *,
    settings: Settings,
    profile_id: str,
) -> ChatInputCatalog:
    """Collect capability hints and profile-local file paths for prompt completion."""

    return ChatInputCatalog(
        skill_names=await _collect_skill_names(settings=settings, profile_id=profile_id),
        subagent_names=await _collect_subagent_names(settings=settings, profile_id=profile_id),
        app_names=_collect_app_names(settings=settings, profile_id=profile_id),
        mcp_server_names=_collect_mcp_server_names(settings=settings, profile_id=profile_id),
        mcp_tool_names=await _collect_mcp_tool_names(settings=settings, profile_id=profile_id),
        file_paths=_collect_profile_file_paths_safe(settings=settings, profile_id=profile_id),
    )


async def _collect_skill_names(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        skills = await get_profile_skill_service(settings).list(
            profile_id=profile_id,
            scope="all",
            include_unavailable=False,
        )
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped skills for profile %s: %s", profile_id, exc)
        return ()
    return tuple(sorted({item.name for item in skills if item.available}))


async def _collect_subagent_names(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        subagents = await get_profile_subagent_service(settings).list(profile_id=profile_id)
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped subagents for profile %s: %s", profile_id, exc)
        return ()
    return tuple(sorted({item.name for item in subagents}))


def _collect_app_names(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        app_registry = get_app_registry(settings=settings, profile_id=profile_id)
        return tuple(sorted(item.name for item in app_registry.list()))
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped apps for profile %s: %s", profile_id, exc)
        return ()


def _collect_mcp_server_names(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        loader = MCPProfileLoader(settings)
        return tuple(sorted({item.server for item in loader.load_profile(profile_id) if item.enabled}))
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped MCP servers for profile %s: %s", profile_id, exc)
        return ()


async def _collect_mcp_tool_names(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        catalog = get_mcp_runtime_catalog(settings)
        descriptors = catalog.list_cached_tools(profile_id=profile_id)
        catalog.schedule_refresh(profile_id=profile_id)
        return tuple(sorted(item.runtime_name for item in descriptors))
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped MCP runtime tools for profile %s: %s", profile_id, exc)
        return ()


def _collect_profile_file_paths_safe(*, settings: Settings, profile_id: str) -> tuple[str, ...]:
    try:
        profile_root = get_profile_runtime_config_service(settings).profile_root(profile_id)
    except Exception as exc:
        _LOGGER.warning("chat input catalog skipped files for profile %s: %s", profile_id, exc)
        return ()
    return _collect_profile_file_paths(profile_root)


def _collect_profile_file_paths(profile_root: Path) -> tuple[str, ...]:
    """Return bounded profile-relative file paths for completion menus."""

    if not profile_root.exists():
        return ()

    profile_root_resolved = profile_root.resolve()
    collected: list[str] = []
    pending_directories: list[Path] = [profile_root]
    while pending_directories and len(collected) < _FILE_SCAN_LIMIT:
        current_directory = pending_directories.pop()
        try:
            directory_entries = sorted(current_directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            _LOGGER.warning(
                "chat input catalog skipped directory %s during file scan: %s",
                current_directory,
                exc,
            )
            continue

        child_directories: list[Path] = []
        for path in directory_entries:
            try:
                relative_path = path.relative_to(profile_root)
            except ValueError:
                continue

            if _should_skip_symlink(path, profile_root_resolved):
                continue
            if path.is_dir():
                if _should_skip_directory(relative_path):
                    continue
                child_directories.append(path)
                continue

            if _should_skip_file(relative_path):
                continue
            collected.append(relative_path.as_posix())
            if len(collected) >= _FILE_SCAN_LIMIT:
                break

        pending_directories.extend(reversed(child_directories))
    return tuple(collected)


def _should_skip_directory(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return False
    directory_name = parts[-1]
    if directory_name in _SKIP_DIRECTORY_NAMES:
        return True
    if directory_name.startswith(".") and directory_name != ".well-known":
        return True
    return any(part in _SKIP_DIRECTORY_NAMES for part in parts[:-1])


def _should_skip_file(relative_path: Path) -> bool:
    parts = relative_path.parts
    if any(part in _SKIP_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    if any(part.startswith(".") and part != ".well-known" for part in parts[:-1]):
        return True
    return False


def _should_skip_symlink(path: Path, profile_root: Path) -> bool:
    """Return whether one completion candidate should be skipped due to symlink safety risk."""

    if not path.is_symlink():
        return False
    if path.is_dir():
        return True
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return True
    return not resolved.is_relative_to(profile_root)
