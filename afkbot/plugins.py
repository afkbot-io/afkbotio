"""Optional AFKBOT extension loading.

The core package owns local/self-hosted runtime behavior. Managed platform
integrations register themselves through Python entry points so Cloud-specific
code can live in separate packages and Docker images.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol

import typer

from afkbot.settings import Settings

logger = logging.getLogger(__name__)

CLI_ENTRYPOINT_GROUP = "afkbot.cli"
RUNTIME_ENTRYPOINT_GROUP = "afkbot.runtime"


@dataclass(slots=True)
class RuntimeExtensionHandle:
    """Running optional runtime extension.

    :param name: Stable extension name used in logs.
    :param task: Background task owned by the extension.
    :param ready: Optional readiness awaitable. Startup waits for it before the
        local API server is considered ready.
    :param request_stop: Optional synchronous shutdown callback.
    :param close: Optional asynchronous cleanup callback.
    :return: None.
    """

    name: str
    task: asyncio.Task[object]
    ready: Awaitable[None] | None = None
    request_stop: Callable[[], None] | None = None
    close: Callable[[], Awaitable[None]] | None = None


class RuntimeExtension(Protocol):
    """Protocol implemented by optional runtime extension packages."""

    setup_guard_exempt_commands: frozenset[str]

    async def before_runtime_start(self, *, settings: Settings, profile_id: str) -> None:
        """Run before local runtime services are started.

        :param settings: Resolved runtime settings.
        :param profile_id: Active runtime profile id.
        :return: None.
        """

    async def start_runtime(
        self,
        *,
        settings: Settings,
        request_shutdown: Callable[[], None],
    ) -> RuntimeExtensionHandle | None:
        """Start extension background work for one runtime process.

        :param settings: Resolved runtime settings.
        :param request_shutdown: Callback that stops the local runtime stack.
        :return: Extension handle or None when inactive.
        """


def register_cli_plugins(app: typer.Typer) -> None:
    """Register CLI commands exposed by installed extension packages.

    :param app: Root Typer app.
    :return: None.
    """

    for entry_point in _entry_points(CLI_ENTRYPOINT_GROUP):
        register = _load_entry_point(entry_point)
        if register is None:
            continue
        try:
            register(app)
        except Exception:
            logger.exception("Failed to register AFKBOT CLI plugin %s.", entry_point.name)
            raise


def setup_guard_exempt_command_names() -> frozenset[str]:
    """Return root command names that extension packages expose before setup.

    :return: Command names that do not require local setup.
    """

    commands: set[str] = set()
    for extension in _runtime_extensions():
        commands.update(getattr(extension, "setup_guard_exempt_commands", frozenset()))
    return frozenset(commands)


async def run_before_runtime_start_extensions(*, settings: Settings, profile_id: str) -> None:
    """Run pre-start hooks for installed runtime extensions.

    :param settings: Resolved runtime settings.
    :param profile_id: Active runtime profile id.
    :return: None.
    """

    for extension in _runtime_extensions():
        before_start = getattr(extension, "before_runtime_start", None)
        if before_start is None:
            continue
        await before_start(settings=settings, profile_id=profile_id)


async def start_runtime_extensions(
    *,
    settings: Settings,
    request_shutdown: Callable[[], None],
) -> list[RuntimeExtensionHandle]:
    """Start installed runtime extensions that opt into this process.

    :param settings: Resolved runtime settings.
    :param request_shutdown: Callback that stops the local runtime stack.
    :return: Started extension handles.
    """

    handles: list[RuntimeExtensionHandle] = []
    for extension in _runtime_extensions():
        start_runtime = getattr(extension, "start_runtime", None)
        if start_runtime is None:
            continue
        handle = await start_runtime(settings=settings, request_shutdown=request_shutdown)
        if handle is not None:
            handles.append(handle)
    return handles


def _runtime_extensions() -> list[Any]:
    extensions: list[Any] = []
    for entry_point in _entry_points(RUNTIME_ENTRYPOINT_GROUP):
        extension = _load_entry_point(entry_point)
        if extension is not None:
            extensions.append(extension)
    return extensions


def _entry_points(group: str) -> list[EntryPoint]:
    return list(entry_points(group=group))


def _load_entry_point(entry_point: EntryPoint) -> Any | None:
    try:
        return entry_point.load()
    except Exception:
        logger.exception("Failed to load AFKBOT plugin entry point %s.", entry_point.name)
        raise
