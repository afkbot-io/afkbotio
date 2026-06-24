"""Contracts for installable AFKBOT runtime plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from afkbot.services.config_schema import JsonConfigField

if TYPE_CHECKING:
    from fastapi import APIRouter

    from afkbot.services.apps.registry_core import AppRegistry
    from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory
    from afkbot.services.tools.base import ToolBase
    from afkbot.settings import Settings

    ToolFactory = Callable[[Settings], ToolBase]
    AppRegistrar = Callable[[AppRegistry], None]

LifecycleHook = Callable[["Settings"], object | Awaitable[object]]


_PLUGIN_TOKEN_RE = r"^[a-z0-9][a-z0-9_-]{0,63}$"


class PluginCapabilities(BaseModel):
    """Declared plugin runtime surfaces."""

    model_config = ConfigDict(extra="forbid")

    api_router: bool = False
    static_web: bool = False
    tools: bool = False
    skills: bool = False
    apps: bool = False
    lifecycle: bool = False
    channels: bool = False


class PluginPermissions(BaseModel):
    """Declared plugin access intents for operator review."""

    model_config = ConfigDict(extra="forbid")

    database: Literal["none", "read", "read_write"] = "none"
    taskflow: Literal["none", "read", "read_write"] = "none"
    outbound_http: bool = False
    data_dir_write: bool = False


class PluginConfigField(JsonConfigField):
    """One validated config field contract declared by a plugin manifest."""


class PluginConfigSchema(BaseModel):
    """Schema for plugin-managed JSON config."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, PluginConfigField] = Field(default_factory=dict)


class PluginMounts(BaseModel):
    """HTTP mount locations for one plugin."""

    model_config = ConfigDict(extra="forbid")

    api_prefix: str | None = None
    web_prefix: str | None = None

    @field_validator("api_prefix", "web_prefix")
    @classmethod
    def _validate_mount_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not normalized.startswith("/"):
            raise ValueError("mount prefixes must start with '/'")
        return normalized.rstrip("/") or "/"


class PluginAuth(BaseModel):
    """Auth policy declared by one plugin manifest."""

    model_config = ConfigDict(extra="forbid")

    operator_required: bool = False
    public: bool = False


class PluginPaths(BaseModel):
    """Relative paths inside one installed plugin package."""

    model_config = ConfigDict(extra="forbid")

    python_root: str = "python"
    web_root: str | None = None
    skills_root: str | None = None

    @field_validator("python_root", "web_root", "skills_root")
    @classmethod
    def _normalize_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().strip("/")
        if not normalized:
            return None
        return normalized


class PluginManifest(BaseModel):
    """Validated plugin manifest loaded from `.afkbot-plugin/plugin.json`."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=64, pattern=_PLUGIN_TOKEN_RE)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=64)
    afkbot_version: str = Field(default="*", min_length=1, max_length=128)
    kind: Literal["embedded"] = "embedded"
    entrypoint: str = Field(min_length=1, max_length=255)
    description: str = ""
    default_config: dict[str, object] = Field(default_factory=dict)
    config_schema: PluginConfigSchema = Field(default_factory=PluginConfigSchema)
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)
    capabilities: PluginCapabilities = Field(default_factory=PluginCapabilities)
    auth: PluginAuth = Field(default_factory=PluginAuth)
    mounts: PluginMounts = Field(default_factory=PluginMounts)
    paths: PluginPaths = Field(default_factory=PluginPaths)

    @field_validator("plugin_id", mode="before")
    @classmethod
    def _normalize_plugin_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, value: str) -> str:
        normalized = value.strip()
        if ":" not in normalized:
            raise ValueError("entrypoint must use 'module:function' syntax")
        module_name, function_name = normalized.split(":", 1)
        if not module_name.strip() or not function_name.strip():
            raise ValueError("entrypoint must include both module and function")
        return normalized

    @model_validator(mode="after")
    def _validate_default_config(self) -> "PluginManifest":
        schema_fields = self.config_schema.fields
        if schema_fields:
            default_keys = set(self.default_config)
            schema_keys = set(schema_fields)
            if default_keys != schema_keys:
                missing = sorted(schema_keys - default_keys)
                extra = sorted(default_keys - schema_keys)
                fragments: list[str] = []
                if missing:
                    fragments.append(f"missing defaults for: {', '.join(missing)}")
                if extra:
                    fragments.append(f"undeclared default_config keys: {', '.join(extra)}")
                raise ValueError("config_schema/default_config mismatch: " + "; ".join(fragments))
        self.validate_config(self.default_config)
        return self

    def validate_config(self, payload: object) -> dict[str, object]:
        """Validate one plugin config object against the manifest contract."""

        if not isinstance(payload, dict):
            raise ValueError("Plugin config payload must be a JSON object")
        validated = dict(payload)
        schema_fields = self.config_schema.fields
        if not schema_fields:
            return validated
        unknown = sorted(set(validated) - set(schema_fields))
        if unknown:
            raise ValueError(f"Unknown plugin config keys: {', '.join(unknown)}")
        for key, field_spec in schema_fields.items():
            if key not in validated:
                raise ValueError(f"Missing plugin config key: {key}")
            validated[key] = field_spec.validate_value(key=key, value=validated[key])
        return validated


class InstalledPluginRecord(BaseModel):
    """Persistent install registry record for one plugin."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(min_length=1, max_length=64, pattern=_PLUGIN_TOKEN_RE)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    enabled: bool = True
    source_kind: Literal["local_path", "github_archive"] = "local_path"
    source_ref: str = Field(min_length=1)
    install_path: str = Field(min_length=1)
    installed_at: datetime
    manifest: PluginManifest


class PluginRegistryFile(BaseModel):
    """Serialized JSON payload for installed plugin records."""

    model_config = ConfigDict(extra="forbid")

    plugins: list[InstalledPluginRecord] = Field(default_factory=list)


class PluginConfigMetadata(BaseModel):
    """Resolved config payload for one installed plugin."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    source: Literal["default", "persisted"]
    config_path: str
    data_dir: str
    config_schema: PluginConfigSchema = Field(default_factory=PluginConfigSchema)
    config: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginStaticMount:
    """One static web mount emitted by a loaded plugin."""

    mount_path: str
    directory: Path
    name: str


@dataclass(frozen=True, slots=True)
class PluginAuthMount:
    """Auth-relevant mount prefixes contributed by one active plugin."""

    plugin_id: str
    api_prefix: str | None
    web_prefix: str | None
    operator_required: bool
    public: bool


@dataclass(frozen=True, slots=True)
class LoadedPluginRuntime:
    """Runtime surfaces contributed by one active plugin."""

    record: InstalledPluginRecord
    routers: tuple[APIRouter, ...]
    static_mounts: tuple[PluginStaticMount, ...]
    skill_dirs: tuple[Path, ...]
    tool_factories: dict[str, ToolFactory]
    channel_adapters: dict[tuple[str, str], ChannelAdapterFactory]
    app_registrars: tuple[AppRegistrar, ...]
    startup_hooks: tuple[LifecycleHook, ...]
    shutdown_hooks: tuple[LifecycleHook, ...]


@dataclass(frozen=True, slots=True)
class PluginRuntimeSnapshot:
    """Merged runtime surfaces across every active plugin."""

    plugins: tuple[LoadedPluginRuntime, ...]

    @property
    def routers(self) -> tuple[APIRouter, ...]:
        return tuple(router for plugin in self.plugins for router in plugin.routers)

    @property
    def static_mounts(self) -> tuple[PluginStaticMount, ...]:
        return tuple(item for plugin in self.plugins for item in plugin.static_mounts)

    @property
    def plugin_auth_mounts(self) -> tuple[PluginAuthMount, ...]:
        """Return auth-relevant mount prefixes for every active plugin."""

        return tuple(
            PluginAuthMount(
                plugin_id=plugin.record.plugin_id,
                api_prefix=plugin.record.manifest.mounts.api_prefix,
                web_prefix=plugin.record.manifest.mounts.web_prefix,
                operator_required=plugin.record.manifest.auth.operator_required,
                public=plugin.record.manifest.auth.public,
            )
            for plugin in self.plugins
        )

    @property
    def operator_auth_plugin_ids(self) -> tuple[str, ...]:
        """Return plugin ids that declare operator auth in their manifest."""

        return tuple(
            plugin.record.plugin_id
            for plugin in self.plugins
            if plugin.record.manifest.auth.operator_required
        )

    @property
    def skill_dirs(self) -> tuple[Path, ...]:
        return tuple(item for plugin in self.plugins for item in plugin.skill_dirs)

    @property
    def tool_factories(self) -> dict[str, ToolFactory]:
        merged: dict[str, ToolFactory] = {}
        for plugin in self.plugins:
            merged.update(plugin.tool_factories)
        return merged

    @property
    def channel_adapters(self) -> dict[tuple[str, str], ChannelAdapterFactory]:
        merged: dict[tuple[str, str], ChannelAdapterFactory] = {}
        for plugin in self.plugins:
            merged.update(plugin.channel_adapters)
        return merged

    @property
    def app_registrars(self) -> tuple[AppRegistrar, ...]:
        return tuple(item for plugin in self.plugins for item in plugin.app_registrars)

    @property
    def startup_hooks(self) -> tuple[LifecycleHook, ...]:
        return tuple(item for plugin in self.plugins for item in plugin.startup_hooks)

    @property
    def shutdown_hooks(self) -> tuple[LifecycleHook, ...]:
        return tuple(item for plugin in reversed(self.plugins) for item in plugin.shutdown_hooks)

    async def run_startup(self, *, settings: Settings) -> None:
        """Run plugin startup hooks in install/runtime order."""

        executed: list[LoadedPluginRuntime] = []
        try:
            for plugin in self.plugins:
                executed.append(plugin)
                for hook in plugin.startup_hooks:
                    await _invoke_lifecycle_hook(hook=hook, settings=settings)
        except Exception:
            for plugin in reversed(executed):
                for hook in plugin.shutdown_hooks:
                    try:
                        await _invoke_lifecycle_hook(hook=hook, settings=settings)
                    except Exception:
                        continue
            raise

    async def run_shutdown(self, *, settings: Settings) -> None:
        """Run plugin shutdown hooks in reverse install/runtime order."""

        for plugin in reversed(self.plugins):
            for hook in plugin.shutdown_hooks:
                await _invoke_lifecycle_hook(hook=hook, settings=settings)


class PluginServiceError(RuntimeError):
    """Deterministic plugin install/load error."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


async def _invoke_lifecycle_hook(*, hook: LifecycleHook, settings: Settings) -> None:
    result = hook(settings)
    if inspect.isawaitable(result):
        await result
