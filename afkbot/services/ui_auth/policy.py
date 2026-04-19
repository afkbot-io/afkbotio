"""Policy helpers for deciding which plugin surfaces require UI auth."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

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
    protected_web_plugin_ids: Collection[str] | None = None,
) -> UIAuthProtectedSurface:
    """Resolve whether one request path belongs to an auth-protected plugin surface."""

    normalized = str(path or "").strip() or "/"
    if not ui_auth_is_configured(settings):
        return UIAuthProtectedSurface(protected=False, api_request=normalized.startswith("/v1/"))

    if normalized == "/v1/plugins" or normalized.startswith("/v1/plugins/"):
        return UIAuthProtectedSurface(protected=True, api_request=True)

    if normalized.startswith("/plugins/"):
        plugin_id = _path_segment(normalized, prefix="/plugins/")
        protected_ids = {
            plugin_id_value.strip().lower()
            for plugin_id_value in settings.ui_auth_protected_plugin_ids
            if plugin_id_value.strip()
        }
        if protected_web_plugin_ids:
            protected_ids.update(
                plugin_id_value.strip().lower()
                for plugin_id_value in protected_web_plugin_ids
                if str(plugin_id_value).strip()
            )
        return UIAuthProtectedSurface(
            protected=plugin_id in protected_ids,
            api_request=False,
            plugin_id=plugin_id,
        )
    return UIAuthProtectedSurface(protected=False, api_request=normalized.startswith("/v1/"))


def _path_segment(path: str, *, prefix: str) -> str | None:
    remainder = path[len(prefix) :]
    if not remainder:
        return None
    segment = remainder.split("/", 1)[0].strip().lower()
    return segment or None
