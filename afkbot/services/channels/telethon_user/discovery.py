"""Read-only Telethon dialog discovery for operator CLI flows."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.telethon_user.client import create_telethon_client
from afkbot.services.channels.telethon_user.contracts import resolve_telethon_credentials
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.runtime_support import validate_telethon_profile_policy
from afkbot.services.channels.telethon_user.watcher import (
    build_dialog_match_text,
    classify_dialog_kind,
    dialog_notifications_enabled,
    matches_chat_title_filters,
    normalize_chat_id,
    normalize_dialog_title,
    select_watched_dialog,
)
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class TelethonDialogRecord:
    """One read-only dialog descriptor for Telethon operator discovery."""

    chat_id: str
    chat_kind: str
    title: str
    username: str | None
    match_text: str
    muted: bool
    watcher_match: bool
    reply_match: bool

    def to_payload(self) -> dict[str, object]:
        """Serialize one dialog record for CLI/API payloads."""

        return asdict(self)


async def list_telethon_dialogs(
    *,
    settings: Settings,
    endpoint: TelethonUserEndpointConfig,
    query: str | None,
    watched_only: bool,
    limit: int,
) -> list[TelethonDialogRecord]:
    """List dialogs accessible to one authorized Telethon endpoint."""

    await validate_telethon_profile_policy(
        settings=settings,
        profile_id=endpoint.profile_id,
    )
    credentials = await resolve_telethon_credentials(
        settings=settings,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        require_session=True,
    )
    client = create_telethon_client(
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        session_string=credentials.session_string,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise TelethonUserServiceError(
                error_code="telethon_session_unauthorized",
                reason="Stored Telethon session is not authorized anymore.",
            )
        get_dialogs = getattr(client, "get_dialogs", None)
        if not callable(get_dialogs):
            raise TelethonUserServiceError(
                error_code="telethon_dialog_listing_failed",
                reason="Telethon client does not support get_dialogs().",
            )
        dialogs = await get_dialogs(limit=None)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    normalized_query = (query or "").strip().lower()
    result: list[TelethonDialogRecord] = []
    for dialog in dialogs:
        chat_kind = classify_dialog_kind(dialog)
        chat_id = normalize_chat_id(dialog)
        if chat_kind is None or chat_id is None:
            continue
        match_text = build_dialog_match_text(dialog)
        if normalized_query and normalized_query not in match_text.lower():
            continue
        watcher_match = (
            select_watched_dialog(dialog=dialog, config=endpoint.watcher) is not None
            if endpoint.watcher.enabled
            else False
        )
        if watched_only and not watcher_match:
            continue
        username_raw = getattr(getattr(dialog, "entity", None), "username", None)
        result.append(
            TelethonDialogRecord(
                chat_id=chat_id,
                chat_kind=chat_kind,
                title=normalize_dialog_title(dialog),
                username=str(username_raw).strip() or None if username_raw is not None else None,
                match_text=match_text,
                muted=not dialog_notifications_enabled(dialog),
                watcher_match=watcher_match,
                reply_match=matches_chat_title_filters(
                    title=match_text,
                    blocked_patterns=endpoint.reply_blocked_chat_patterns,
                    allowed_patterns=endpoint.reply_allowed_chat_patterns,
                ),
            )
        )
        if len(result) >= max(1, limit):
            break
    return result
