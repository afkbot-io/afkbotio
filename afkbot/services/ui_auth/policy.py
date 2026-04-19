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

    mounts = tuple(plugin_auth_mounts or ())
    protected_ids = {
        plugin_id_value.strip().lower()
        for plugin_id_value in settings.ui_auth_protected_plugin_ids
        if plugin_id_value.strip()
    }
    protected_ids.update(
        mount.plugin_id.strip().lower()
        for mount in mounts
        if mount.operator_required and mount.plugin_id.strip()
    )

    for mount in mounts:
        mount_plugin_id = mount.plugin_id.strip().lower()
        mount_protected = bool(mount_plugin_id and mount_plugin_id in protected_ids)
        if _path_matches_prefix(normalized, mount.api_prefix):
            return UIAuthProtectedSurface(
                protected=mount_protected,
                api_request=True,
                plugin_id=mount.plugin_id if mount_protected else None,
            )
        if _path_matches_prefix(normalized, mount.web_prefix):
            return UIAuthProtectedSurface(
                protected=mount_protected,
                api_request=False,
                plugin_id=mount.plugin_id if mount_protected else None,
            )
    return UIAuthProtectedSurface(protected=False, api_request=normalized.startswith("/v1/"))


def _path_matches_prefix(path: str, prefix: str | None) -> bool:
    normalized_prefix = str(prefix or "").strip()
    if not normalized_prefix:
        return False
    if path == normalized_prefix:
        return True
    return path.startswith(f"{normalized_prefix}/")
