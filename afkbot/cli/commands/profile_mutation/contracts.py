"""Typed contracts shared by profile add, update, and setup CLI flows."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.setup.profile_resolution import (
    ResolvedProfilePolicyInputs,
    ResolvedProfileRuntimeCore,
)


@dataclass(frozen=True)
class CollectedProfileMutationInputs:
    """Resolved shared inputs for one profile add/update mutation."""

    resolved_name: str
    runtime_core: ResolvedProfileRuntimeCore
    provider_api_key: str
    runtime_secrets_update: dict[str, str]
    resolved_policy: ResolvedProfilePolicyInputs
    runtime_config: ProfileRuntimeConfig


__all__ = ["CollectedProfileMutationInputs"]
