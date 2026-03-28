"""Shared Telethon runtime helpers used by auth and live channel runtime."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.normalization import TelethonUserIdentity
from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.settings import Settings


def resolve_telethon_identity(entity: object) -> TelethonUserIdentity:
    """Normalize one Telethon `get_me()` entity into stable identity payload."""

    user_id = getattr(entity, "id", None)
    if not isinstance(user_id, int):
        raise TelethonUserServiceError(
            error_code="telethon_identity_invalid",
            reason="Telethon get_me returned an invalid user id.",
        )
    first_name = getattr(entity, "first_name", None)
    last_name = getattr(entity, "last_name", None)
    display_name_parts = [
        str(part).strip()
        for part in (first_name, last_name)
        if isinstance(part, str) and part.strip()
    ]
    display_name = " ".join(display_name_parts) or None
    username = getattr(entity, "username", None)
    phone = getattr(entity, "phone", None)
    return TelethonUserIdentity(
        user_id=user_id,
        username=str(username).strip() or None if username is not None else None,
        phone=str(phone).strip() or None if phone is not None else None,
        display_name=display_name,
    )


async def validate_telethon_profile_policy(
    *,
    settings: Settings,
    profile_id: str,
) -> None:
    """Reject Telethon runtime when profile policy cannot safely express MTProto access."""

    allowed, reason = await evaluate_telethon_profile_policy(
        settings=settings,
        profile_id=profile_id,
    )
    if allowed:
        return
    raise TelethonUserServiceError(
        error_code="telethon_policy_network_unsupported",
        reason=reason
        or (
            "Telethon uses MTProto and is not compatible with host-only network allowlists. "
            "Use a profile with policy disabled or network allowlist containing `*`."
        ),
    )


async def evaluate_telethon_profile_policy(
    *,
    settings: Settings,
    profile_id: str,
) -> tuple[bool, str | None]:
    """Return whether one profile policy can run Telethon plus operator-facing reason."""

    try:
        profile = await get_profile_service(settings).get(profile_id=profile_id)
    except ProfileServiceError as exc:
        raise TelethonUserServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    if not profile.policy.enabled:
        return True, None
    if "*" in profile.policy.network_allowlist:
        return True, None
    return (
        False,
        (
            "Telethon uses MTProto and is not compatible with host-only network allowlists. "
            "Use a profile with policy disabled or network allowlist containing `*`."
        ),
    )


async def persist_telethon_identity_state(
    *,
    state_path: Path,
    account_id: str,
    identity: TelethonUserIdentity | None,
    last_error: str | None,
) -> None:
    """Persist the latest Telethon identity/health snapshot."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "account_id": account_id,
        "last_connected_at": datetime.now(UTC).isoformat(),
        "last_error": last_error,
    }
    if identity is not None:
        payload["identity"] = {
            "user_id": identity.user_id,
            "username": identity.username,
            "phone": identity.phone,
            "display_name": identity.display_name,
        }
    state_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )


async def event_replies_to_user(event: object, *, user_id: int) -> bool:
    """Return whether one Telethon event replies to the current user identity."""

    if not bool(getattr(event, "is_reply", False)):
        return False
    get_reply_message = getattr(event, "get_reply_message", None)
    if not callable(get_reply_message):
        return False
    reply = await get_reply_message()
    if reply is None:
        return False
    return getattr(reply, "sender_id", None) == user_id
