"""Shared CLI help text for policy-related command options."""

from __future__ import annotations

from afkbot.services.policy import PolicyCapabilityId, list_capability_specs

_LEGACY_CAPABILITY_ALIASES: tuple[PolicyCapabilityId, ...] = (
    PolicyCapabilityId.EMAIL,
    PolicyCapabilityId.TELEGRAM,
)


def policy_capability_option_help() -> str:
    """Return canonical CLI help text for repeatable policy capability flags."""

    canonical_values = ",".join(
        spec.id.value
        for spec in list_capability_specs()
        if spec.id not in _LEGACY_CAPABILITY_ALIASES
    )
    legacy_values = ",".join(alias.value for alias in _LEGACY_CAPABILITY_ALIASES)
    return (
        "Capability id (repeatable): "
        f"{canonical_values} "
        f"(legacy aliases: {legacy_values})"
    )


__all__ = ["policy_capability_option_help"]
