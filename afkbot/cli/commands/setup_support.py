"""Shared support helpers for the public CLI setup command."""

from __future__ import annotations

from afkbot.cli.commands.profile_mutation_support import (
    build_policy_defaults_from_details,
    build_runtime_defaults_from_details,
)
from afkbot.services.profile_runtime import (
    ProfileDetails,
    ProfileServiceError,
    run_profile_service_sync,
)
from afkbot.services.setup.defaults import load_env_defaults
from afkbot.settings import Settings


def load_setup_defaults(settings: Settings) -> dict[str, str]:
    """Load setup defaults from runtime store plus current `default` profile when present."""

    defaults = load_env_defaults(settings=settings)
    details = load_current_default_profile(settings)
    if details is None:
        return defaults

    merged = dict(defaults)
    profile_defaults = build_runtime_defaults_from_details(details)
    profile_defaults.update(
        build_policy_defaults_from_details(
            root_dir=settings.root_dir,
            details=details,
        )
    )
    for key, value in profile_defaults.items():
        merged[key] = value
    return merged


def load_current_default_profile(settings: Settings) -> ProfileDetails | None:
    """Return the current default profile when it exists."""

    try:
        return run_profile_service_sync(
            settings,
            lambda service: service.get(profile_id="default"),
        )
    except (ProfileServiceError, OSError):
        return None


def format_setup_runtime_error(exc: ProfileServiceError | OSError | RuntimeError) -> str:
    """Render one setup/runtime failure without swallowing unexpected exception classes."""

    if isinstance(exc, ProfileServiceError):
        return str(exc.reason)
    if isinstance(exc, OSError):
        return f"{exc.__class__.__name__}: {exc}"
    return str(exc)
