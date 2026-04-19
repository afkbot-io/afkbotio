"""Tests for UI auth surface policy resolution."""

from __future__ import annotations

from afkbot.services.plugins.contracts import PluginAuthMount
from afkbot.services.ui_auth.policy import resolve_ui_auth_surface
from afkbot.settings import Settings


def _configured_settings(*, protected_plugin_ids: tuple[str, ...] = ()) -> Settings:
    return Settings(
        ui_auth_mode="password",
        ui_auth_username="operator",
        ui_auth_password_hash="scrypt$fixture",
        ui_auth_protected_plugin_ids=protected_plugin_ids,
    )


def test_plugin_api_surface_uses_combined_runtime_and_manifest_protection() -> None:
    """Plugin API auth should be required from manifest operator_required or runtime protected ids."""

    settings = _configured_settings(protected_plugin_ids=())
    mount_manifest_only = PluginAuthMount(
        plugin_id="demo",
        api_prefix="/v1/plugins/demo",
        web_prefix="/plugins/demo",
        operator_required=True,
    )
    mount_runtime_only = PluginAuthMount(
        plugin_id="runtime-only",
        api_prefix="/v1/plugins/runtime-only",
        web_prefix="/plugins/runtime-only",
        operator_required=False,
    )

    surface_without_protection = resolve_ui_auth_surface(
        "/v1/plugins/public/ping",
        settings,
        plugin_auth_mounts=(mount_manifest_only, mount_runtime_only),
    )
    surface_manifest_protected = resolve_ui_auth_surface(
        "/v1/plugins/demo/ping",
        settings,
        plugin_auth_mounts=(mount_manifest_only, mount_runtime_only),
    )
    surface_runtime_protected = resolve_ui_auth_surface(
        "/v1/plugins/runtime-only/ping",
        _configured_settings(protected_plugin_ids=("runtime-only",)),
        plugin_auth_mounts=(mount_manifest_only, mount_runtime_only),
    )

    assert surface_without_protection.api_request is True
    assert surface_without_protection.protected is False

    assert surface_manifest_protected.api_request is True
    assert surface_manifest_protected.plugin_id == "demo"
    assert surface_manifest_protected.protected is True

    assert surface_runtime_protected.api_request is True
    assert surface_runtime_protected.plugin_id == "runtime-only"
    assert surface_runtime_protected.protected is True
