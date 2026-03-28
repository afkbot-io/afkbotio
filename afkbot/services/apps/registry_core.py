"""Core registry primitives for builtin and profile app modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import BaseModel

from afkbot.services.apps.credential_manifest import AppCredentialManifest
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

AppHandler = Callable[[Settings, AppRuntimeContext, str, dict[str, object]], Awaitable[ToolResult]]


def normalize_name(value: str) -> str:
    """Normalize one registry name or alias token."""

    return value.strip().lower()


def normalize_set(values: Iterable[str]) -> frozenset[str]:
    """Normalize non-empty string set into deterministic frozenset."""

    normalized: set[str] = set()
    for raw in values:
        value = normalize_name(raw)
        if value:
            normalized.add(value)
    return frozenset(normalized)


@dataclass(frozen=True, slots=True)
class AppDefinition:
    """Registry definition for one integration app."""

    name: str
    handler: AppHandler
    allowed_skills: frozenset[str]
    allowed_actions: frozenset[str]
    action_params_models: Mapping[str, type[BaseModel]] = field(default_factory=dict)
    credential_manifest: AppCredentialManifest | None = None
    source: str = "builtin"
    source_path: str | None = None

    def __post_init__(self) -> None:
        normalized_name = normalize_name(self.name)
        if not normalized_name:
            raise ValueError("App name is empty")

        normalized_skills = normalize_set(self.allowed_skills)
        if not normalized_skills:
            raise ValueError(f"Allowed skills are empty for app: {self.name}")

        normalized_actions = normalize_set(self.allowed_actions)
        if not normalized_actions:
            raise ValueError(f"Allowed actions are empty for app: {self.name}")

        normalized_action_models = {
            normalize_name(raw_key): raw_value
            for raw_key, raw_value in self.action_params_models.items()
            if normalize_name(raw_key)
        }
        unknown_action_models = sorted(
            {name for name in normalized_action_models if name not in normalized_actions}
        )
        if unknown_action_models:
            targets = ", ".join(unknown_action_models)
            raise ValueError(f"Unknown action schema models for app {self.name}: {targets}")

        normalized_source = normalize_name(self.source) or "builtin"
        normalized_source_path = None if self.source_path is None else str(self.source_path).strip() or None

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "allowed_skills", normalized_skills)
        object.__setattr__(self, "allowed_actions", normalized_actions)
        object.__setattr__(self, "action_params_models", MappingProxyType(dict(normalized_action_models)))
        object.__setattr__(self, "source", normalized_source)
        object.__setattr__(self, "source_path", normalized_source_path)

    def normalize_action(self, action: str) -> str:
        """Return normalized canonical action name."""

        return normalize_name(action)


class AppRegistry:
    """In-memory registry for integration app definitions."""

    def __init__(self) -> None:
        self._apps: dict[str, AppDefinition] = {}

    def register(self, definition: AppDefinition, *, replace_existing: bool = False) -> None:
        """Register one app definition."""

        if definition.name in self._apps and not replace_existing:
            raise ValueError(f"App is already registered: {definition.name}")
        self._apps[definition.name] = definition

    def copy(self) -> AppRegistry:
        """Return shallow copy preserving immutable app definitions."""

        copied = AppRegistry()
        copied._apps = dict(self._apps)
        return copied

    def get(self, app_name: str) -> AppDefinition | None:
        """Get app definition by normalized app name."""

        return self._apps.get(normalize_name(app_name))

    def list(self) -> tuple[AppDefinition, ...]:
        """List all known app definitions in deterministic order."""

        names = sorted(self._apps)
        return tuple(self._apps[name] for name in names)


def build_register_app(
    *,
    registry: AppRegistry,
    source: str,
    source_path: str | None = None,
) -> Callable[..., Callable[[AppHandler], AppHandler]]:
    """Build one decorator factory bound to target registry/source metadata."""

    def factory(
        *,
        name: str,
        allowed_skills: Iterable[str],
        allowed_actions: Iterable[str],
        action_params_models: Mapping[str, type[BaseModel]] | None = None,
        credential_manifest: AppCredentialManifest | None = None,
    ) -> Callable[[AppHandler], AppHandler]:
        action_models = dict(action_params_models or {})

        def decorator(handler: AppHandler) -> AppHandler:
            registry.register(
                AppDefinition(
                    name=name,
                    handler=handler,
                    allowed_skills=frozenset(allowed_skills),
                    allowed_actions=frozenset(allowed_actions),
                    action_params_models=action_models,
                    credential_manifest=credential_manifest,
                    source=source,
                    source_path=source_path,
                ),
                replace_existing=source != "builtin",
            )
            return handler

        return decorator

    return factory
