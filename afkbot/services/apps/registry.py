"""Public app registry entrypoints for builtin and profile-local integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from pydantic import BaseModel

from afkbot.services.apps.credential_manifest import AppCredentialManifest
from afkbot.services.apps.registry_core import (
    AppDefinition,
    AppHandler,
    AppRegistry,
    build_register_app,
)
from afkbot.services.apps.registry_discovery import (
    discover_profile_apps,
    ensure_builtin_apps_loaded,
)
from afkbot.settings import Settings


_builtin_registry = AppRegistry()
__all__ = [
    "AppDefinition",
    "AppHandler",
    "AppRegistry",
    "build_register_app",
    "get_app_registry",
    "register_app",
]


def register_app(
    *,
    name: str,
    allowed_skills: Iterable[str],
    allowed_actions: Iterable[str],
    action_params_models: Mapping[str, type[BaseModel]] | None = None,
    credential_manifest: AppCredentialManifest | None = None,
) -> Callable[[AppHandler], AppHandler]:
    """Decorator to register one app runtime handler in the builtin registry."""

    return build_register_app(
        registry=_builtin_registry,
        source="builtin",
    )(
        name=name,
        allowed_skills=allowed_skills,
        allowed_actions=allowed_actions,
        action_params_models=action_params_models,
        credential_manifest=credential_manifest,
    )


def get_app_registry(
    *,
    settings: Settings | None = None,
    profile_id: str | None = None,
) -> AppRegistry:
    """Return builtin app registry, optionally merged with profile-local app modules."""

    ensure_builtin_apps_loaded()
    if settings is None or profile_id is None:
        return _builtin_registry

    normalized_profile_id = profile_id.strip()
    if not normalized_profile_id:
        return _builtin_registry

    merged = _builtin_registry.copy()
    discover_profile_apps(
        registry=merged,
        settings=settings,
        profile_id=normalized_profile_id,
    )
    return merged
