"""Cutover policy helpers for channel-routing rollout."""

from __future__ import annotations

from afkbot.settings import Settings

_USER_FACING_TRANSPORTS = frozenset(
    {
        "telegram",
        "telegram_user",
        "discord",
        "slack",
        "smtp",
        "email",
        "partyflow",
    }
)


def normalize_transport_name(transport: str | None) -> str | None:
    """Normalize one transport selector into canonical lowercase form."""

    normalized = (transport or "").strip().lower()
    return normalized or None


def is_user_facing_transport(transport: str | None) -> bool:
    """Return whether one transport is an external user-facing ingress."""

    normalized = normalize_transport_name(transport)
    if normalized is None:
        return False
    return normalized in _USER_FACING_TRANSPORTS


def allow_binding_fallback(*, settings: Settings, transport: str | None) -> bool:
    """Return whether unresolved binding selectors may fall back for this transport."""

    normalized = normalize_transport_name(transport)
    if normalized is None:
        return False
    return normalized in {
        item.strip().lower()
        for item in settings.channel_routing_fallback_transports
        if item.strip()
    }


def requires_strict_binding_match(
    *,
    settings: Settings,
    transport: str | None,
    require_binding_match: bool = False,
) -> bool:
    """Return whether binding resolution should fail closed."""

    if require_binding_match:
        return True
    return not allow_binding_fallback(settings=settings, transport=transport)
