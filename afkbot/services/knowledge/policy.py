"""Access policy helpers for derived project knowledge."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.channel_routing.policy import (
    is_user_facing_transport as is_known_user_facing_transport,
    normalize_transport_name,
)

INTERNAL_KNOWLEDGE_TRANSPORTS = {
    "automation",
    "cli",
    "desktop",
    "local",
    "taskflow",
}


@dataclass(frozen=True, slots=True)
class KnowledgeActorContext:
    """Minimal actor context needed for knowledge access decisions."""

    profile_id: str
    transport: str | None = None
    channel_profile: str | None = None
    actor_type: str | None = None
    actor_ref: str | None = None


def is_user_facing_transport(transport: str | None) -> bool:
    """Return whether a transport should be treated as external/user-facing."""

    normalized = normalize_transport_name(transport)
    if normalized is None:
        return False
    if normalized in INTERNAL_KNOWLEDGE_TRANSPORTS:
        return False
    if is_known_user_facing_transport(normalized):
        return True
    return True


def can_access_project_knowledge(
    context: KnowledgeActorContext,
    *,
    target_profile_id: str,
    allow_user_facing: bool = False,
) -> bool:
    """Return whether one actor may access project-derived knowledge."""

    if context.profile_id != target_profile_id:
        return False
    if not context.transport and not allow_user_facing:
        return False
    if is_user_facing_transport(context.transport) and not allow_user_facing:
        return False
    if context.channel_profile in {"messaging_safe", "support_readonly"} and not allow_user_facing:
        return False
    return True
