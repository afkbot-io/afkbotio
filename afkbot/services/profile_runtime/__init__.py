"""Profile-scoped agent/runtime services."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "ProfileBootstrapFileView": ("afkbot.services.profile_runtime.contracts", "ProfileBootstrapFileView"),
    "ProfileBootstrapRecord": ("afkbot.services.profile_runtime.contracts", "ProfileBootstrapRecord"),
    "ProfileDetails": ("afkbot.services.profile_runtime.contracts", "ProfileDetails"),
    "ProfilePolicyView": ("afkbot.services.profile_runtime.contracts", "ProfilePolicyView"),
    "ProfileRuntimeConfig": ("afkbot.services.profile_runtime.contracts", "ProfileRuntimeConfig"),
    "ProfileRuntimeResolved": ("afkbot.services.profile_runtime.contracts", "ProfileRuntimeResolved"),
    "ProfileRuntimeSecretsView": ("afkbot.services.profile_runtime.contracts", "ProfileRuntimeSecretsView"),
    "ProfileSummary": ("afkbot.services.profile_runtime.contracts", "ProfileSummary"),
    "ProfileBootstrapService": ("afkbot.services.profile_runtime.bootstrap_service", "ProfileBootstrapService"),
    "get_profile_bootstrap_service": ("afkbot.services.profile_runtime.bootstrap_service", "get_profile_bootstrap_service"),
    "reset_profile_bootstrap_services": ("afkbot.services.profile_runtime.bootstrap_service", "reset_profile_bootstrap_services"),
    "ProfileRuntimeConfigService": ("afkbot.services.profile_runtime.runtime_config", "ProfileRuntimeConfigService"),
    "get_profile_runtime_config_service": ("afkbot.services.profile_runtime.runtime_config", "get_profile_runtime_config_service"),
    "reset_profile_runtime_config_services": ("afkbot.services.profile_runtime.runtime_config", "reset_profile_runtime_config_services"),
    "ProfileRuntimeSecretsService": ("afkbot.services.profile_runtime.runtime_secrets", "ProfileRuntimeSecretsService"),
    "get_profile_runtime_secrets_service": ("afkbot.services.profile_runtime.runtime_secrets", "get_profile_runtime_secrets_service"),
    "provider_oauth_metadata_fields": ("afkbot.services.profile_runtime.runtime_secrets", "provider_oauth_metadata_fields"),
    "provider_secret_field": ("afkbot.services.profile_runtime.runtime_secrets", "provider_secret_field"),
    "reset_profile_runtime_secrets_services": ("afkbot.services.profile_runtime.runtime_secrets", "reset_profile_runtime_secrets_services"),
    "ProfileService": ("afkbot.services.profile_runtime.service", "ProfileService"),
    "ProfileServiceError": ("afkbot.services.profile_runtime.service", "ProfileServiceError"),
    "get_profile_service": ("afkbot.services.profile_runtime.service", "get_profile_service"),
    "run_profile_service_sync": ("afkbot.services.profile_runtime.service", "run_profile_service_sync"),
    "reset_profile_services": ("afkbot.services.profile_runtime.service", "reset_profile_services"),
    "reset_profile_services_async": ("afkbot.services.profile_runtime.service", "reset_profile_services_async"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load public profile-runtime symbols lazily to avoid package-root import cycles."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
