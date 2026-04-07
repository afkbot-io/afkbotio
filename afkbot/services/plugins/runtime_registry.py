"""Runtime registration surface for embedded AFKBOT plugins."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter

from afkbot.services.path_scope import resolve_in_scope
from afkbot.services.plugins.config_store import read_plugin_config, reset_plugin_config, write_plugin_config
from afkbot.services.plugins.contracts import (
    InstalledPluginRecord,
    LoadedPluginRuntime,
    PluginManifest,
    PluginStaticMount,
    PluginServiceError,
)

if TYPE_CHECKING:
    from afkbot.services.apps.registry_core import AppRegistry
    from afkbot.services.tools.base import ToolBase
    from afkbot.settings import Settings

    ToolFactory = Callable[[Settings], ToolBase]
    AppRegistrar = Callable[[AppRegistry], None]
    LifecycleHook = Callable[[Settings], object]


class PluginRuntimeRegistry:
    """Collect runtime surfaces exposed by one embedded plugin."""

    def __init__(
        self,
        *,
        plugin_root: Path,
        record: InstalledPluginRecord,
        manifest: PluginManifest,
        config_path: Path,
        data_dir: Path,
    ) -> None:
        self._plugin_root = plugin_root.resolve(strict=False)
        self._record = record
        self._manifest = manifest
        self._config_path = config_path.resolve(strict=False)
        self._data_dir = data_dir.resolve(strict=False)
        self._routers: list[APIRouter] = []
        self._static_mounts: list[PluginStaticMount] = []
        self._skill_dirs: list[Path] = []
        self._tool_factories: dict[str, ToolFactory] = {}
        self._app_registrars: list[AppRegistrar] = []
        self._startup_hooks: list[LifecycleHook] = []
        self._shutdown_hooks: list[LifecycleHook] = []

    @property
    def plugin_id(self) -> str:
        return self._manifest.plugin_id

    @property
    def plugin_root(self) -> Path:
        return self._plugin_root

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def data_dir(self) -> Path:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    def read_config(self) -> dict[str, object]:
        """Read resolved plugin config or manifest defaults."""

        try:
            payload, _ = read_plugin_config(
                path=self._config_path,
                default_config=self._manifest.default_config,
            )
            return self._manifest.validate_config(payload)
        except ValueError as exc:
            raise PluginServiceError(
                error_code="plugin_config_invalid",
                reason=str(exc),
            ) from exc

    def write_config(self, payload: dict[str, object]) -> None:
        """Persist plugin config as one JSON object."""

        try:
            merged = dict(self._manifest.default_config)
            merged.update(payload)
            validated = self._manifest.validate_config(merged)
        except ValueError as exc:
            raise PluginServiceError(
                error_code="plugin_config_invalid",
                reason=str(exc),
            ) from exc
        write_plugin_config(path=self._config_path, payload=validated)

    def reset_config(self) -> None:
        """Remove the persisted config file and fall back to manifest defaults."""

        reset_plugin_config(path=self._config_path)

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve one plugin-relative path inside the installed package."""

        raw = Path(path)
        candidate = raw if raw.is_absolute() else self._plugin_root / raw
        return resolve_in_scope(candidate, scope_root=self._plugin_root, strict=False)

    def register_router(self, router: APIRouter) -> None:
        """Register one FastAPI router exposed by the plugin."""

        self._routers.append(router)

    def register_static_dir(
        self,
        *,
        path: str | Path,
        mount_path: str,
        name: str | None = None,
    ) -> None:
        """Register one static web directory mount."""

        normalized_mount = mount_path.strip()
        if not normalized_mount.startswith("/"):
            raise ValueError("Plugin static mount_path must start with '/'")
        directory = self.resolve_path(path)
        self._static_mounts.append(
            PluginStaticMount(
                mount_path=normalized_mount.rstrip("/") or "/",
                directory=directory,
                name=name or f"plugin-{self.plugin_id}-static",
            )
        )

    def register_skill_dir(self, path: str | Path) -> None:
        """Expose one plugin-provided skills root."""

        self._skill_dirs.append(self.resolve_path(path))

    def register_tool_factory(self, factory_id: str, factory: ToolFactory) -> None:
        """Expose one plugin-provided tool factory."""

        normalized = factory_id.strip()
        if not normalized:
            raise ValueError("Plugin tool factory id is required")
        if normalized in self._tool_factories:
            raise ValueError(f"Plugin tool factory is already registered: {normalized}")
        self._tool_factories[normalized] = factory

    def register_app_registrar(self, registrar: AppRegistrar) -> None:
        """Register one callback that mutates the app registry at merge time."""

        self._app_registrars.append(registrar)

    def register_startup_hook(self, hook: LifecycleHook) -> None:
        """Register one callback executed when the main AFKBOT app starts."""

        self._startup_hooks.append(hook)

    def register_shutdown_hook(self, hook: LifecycleHook) -> None:
        """Register one callback executed when the main AFKBOT app stops."""

        self._shutdown_hooks.append(hook)

    def snapshot(self) -> LoadedPluginRuntime:
        """Return immutable runtime surfaces for the current plugin."""

        return LoadedPluginRuntime(
            record=self._record,
            routers=tuple(self._routers),
            static_mounts=tuple(self._static_mounts),
            skill_dirs=tuple(self._skill_dirs),
            tool_factories=dict(self._tool_factories),
            app_registrars=tuple(self._app_registrars),
            startup_hooks=tuple(self._startup_hooks),
            shutdown_hooks=tuple(self._shutdown_hooks),
        )
