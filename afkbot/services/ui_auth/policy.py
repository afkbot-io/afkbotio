"""Policy helpers for deciding which plugin surfaces require UI auth."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from afkbot.services.plugins.contracts import PluginAuthMount
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class UIAuthProtectedSurface:
    """One resolved request surface relative to the auth policy."""

    protected: bool
    api_request: bool
    plugin_id: str | None = None


def ui_auth_is_configured(settings: Settings) -> bool:
    """Return whether UI auth is active and has the required credentials."""

    return (
        settings.ui_auth_mode == "password"
        and bool(settings.ui_auth_username)
        and bool(settings.ui_auth_password_hash)
    )


def resolve_ui_auth_surface(
    path: str,
    settings: Settings,
    *,
    plugin_auth_mounts: Collection[PluginAuthMount] | None = None,
) -> UIAuthProtectedSurface:
    """Resolve whether one request path belongs to an auth-protected plugin surface."""

    normalized = str(path or "").strip() or "/"
    if not ui_auth_is_configured(settings):
        return UIAuthProtectedSurface(protected=False, api_request=normalized.startswith("/v1/"))

    protected_ids = {
        plugin_id_value.strip().lower()
        for plugin_id_value in settings.ui_auth_protected_plugin_ids
        if plugin_id_value.strip()
    }
    protected_mounts = tuple(
        mount
        for mount in (plugin_auth_mounts or ())
        if mount.operator_required or mount.plugin_id in protected_ids
    )

    for mount in protected_mounts:
        if _path_matches_prefix(normalized, mount.api_prefix):
            return UIAuthProtectedSurface(protected=True, api_request=True, plugin_id=mount.plugin_id)
        if _path_matches_prefix(normalized, mount.web_prefix):
            return UIAuthProtectedSurface(protected=True, api_request=False, plugin_id=mount.plugin_id)
    return UIAuthProtectedSurface(protected=False, api_request=normalized.startswith("/v1/"))


def _path_matches_prefix(path: str, prefix: str | None) -> bool:
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        return False
    if path == normalized_prefix:
        return True
    return path.startswith(f"{normalized_prefix}/")
