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
        public=False,
    )
    mount_runtime_only = PluginAuthMount(
        plugin_id="runtime-only",
        api_prefix="/v1/plugins/runtime-only",
        web_prefix="/plugins/runtime-only",
        operator_required=False,
        public=False,
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
    assert surface_without_protection.protected is True
    assert surface_without_protection.plugin_id is None

    assert surface_manifest_protected.api_request is True
    assert surface_manifest_protected.plugin_id is None
    assert surface_manifest_protected.protected is True

    assert surface_runtime_protected.api_request is True
    assert surface_runtime_protected.plugin_id is None
    assert surface_runtime_protected.protected is True


def test_operator_required_plugin_mount_fails_closed_when_ui_auth_is_not_configured() -> None:
    """Operator-only plugin mounts must not become public when UI auth is absent."""

    mount = PluginAuthMount(
        plugin_id="demo",
        api_prefix="/internal/demo",
        web_prefix="/plugins/demo",
        operator_required=True,
        public=False,
    )

    api_surface = resolve_ui_auth_surface(
        "/internal/demo/ping",
        Settings(),
        plugin_auth_mounts=(mount,),
    )
    web_surface = resolve_ui_auth_surface(
        "/plugins/demo/",
        Settings(),
        plugin_auth_mounts=(mount,),
    )

    assert api_surface.protected is True
    assert api_surface.api_request is True
    assert api_surface.auth_configured is False
    assert web_surface.protected is True
    assert web_surface.api_request is False
    assert web_surface.auth_configured is False


def test_required_shared_plugin_api_fails_closed_when_ui_auth_is_not_configured() -> None:
    """The built-in plugin management API must not become public before UI auth setup."""

    surface = resolve_ui_auth_surface(
        "/v1/plugins/demo/config",
        Settings(plugin_api_auth_required=True),
        plugin_auth_mounts=(),
    )

    assert surface.protected is True
    assert surface.api_request is True
    assert surface.auth_configured is False


def test_plugin_mounts_are_private_by_default_and_public_only_by_manifest_opt_out() -> None:
    """Plugin web/API mounts should not become public unless the manifest says so explicitly."""

    private_mount = PluginAuthMount(
        plugin_id="private-demo",
        api_prefix="/private-api/demo",
        web_prefix="/private/demo",
        operator_required=False,
        public=False,
    )
    public_mount = PluginAuthMount(
        plugin_id="public-demo",
        api_prefix="/public-api/demo",
        web_prefix="/public/demo",
        operator_required=False,
        public=True,
    )

    private_surface = resolve_ui_auth_surface(
        "/private/demo/",
        _configured_settings(),
        plugin_auth_mounts=(private_mount, public_mount),
    )
    public_surface = resolve_ui_auth_surface(
        "/public/demo/",
        _configured_settings(),
        plugin_auth_mounts=(private_mount, public_mount),
    )

    assert private_surface.protected is True
    assert private_surface.plugin_id == "private-demo"
    assert public_surface.protected is False
    assert public_surface.api_request is False
