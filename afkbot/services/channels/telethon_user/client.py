"""Lazy Telethon import and client factory helpers."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol, cast

from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError


@dataclass(frozen=True, slots=True)
class TelethonImportBundle:
    """Imported Telethon modules used by the user-channel implementation."""

    telegram_client_cls: type[Any]
    string_session_cls: type[Any]
    events_module: Any
    session_password_needed_error: type[Exception]
    phone_code_invalid_error: type[Exception]
    phone_code_expired_error: type[Exception]
    phone_number_invalid_error: type[Exception]


class TelethonClientLike(Protocol):
    """Typed subset of Telethon client methods used by AFKBOT."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    async def get_me(self) -> object: ...

    async def send_code_request(self, phone: str) -> object: ...

    async def sign_in(self, **kwargs: str) -> object: ...

    async def log_out(self) -> object: ...

    def add_event_handler(self, callback: object, event: object) -> object: ...

    def remove_event_handler(self, callback: object, event: object) -> object: ...

    async def run_until_disconnected(self) -> object: ...

    async def send_message(self, entity: object, message: str) -> object: ...

    async def qr_login(self, ignored_ids: list[int] | None = None) -> object: ...


def import_telethon() -> TelethonImportBundle:
    """Import Telethon lazily so the rest of the app stays importable without it."""

    try:
        telethon_module = importlib.import_module("telethon")
        telethon_errors_module = importlib.import_module("telethon.errors")
        telethon_sessions_module = importlib.import_module("telethon.sessions")
    except ModuleNotFoundError as exc:
        raise TelethonUserServiceError(
            error_code="telethon_package_missing",
            reason="Telethon is not installed. Install project dependencies to enable this channel.",
        ) from exc
    TelegramClient = cast(type[Any], getattr(telethon_module, "TelegramClient"))
    events = getattr(telethon_module, "events")
    SessionPasswordNeededError = cast(
        type[Exception],
        getattr(telethon_errors_module, "SessionPasswordNeededError"),
    )
    PhoneCodeInvalidError = cast(
        type[Exception],
        getattr(telethon_errors_module, "PhoneCodeInvalidError"),
    )
    PhoneCodeExpiredError = cast(
        type[Exception],
        getattr(telethon_errors_module, "PhoneCodeExpiredError"),
    )
    PhoneNumberInvalidError = cast(
        type[Exception],
        getattr(telethon_errors_module, "PhoneNumberInvalidError"),
    )
    StringSession = cast(type[Any], getattr(telethon_sessions_module, "StringSession"))
    return TelethonImportBundle(
        telegram_client_cls=TelegramClient,
        string_session_cls=StringSession,
        events_module=events,
        session_password_needed_error=SessionPasswordNeededError,
        phone_code_invalid_error=PhoneCodeInvalidError,
        phone_code_expired_error=PhoneCodeExpiredError,
        phone_number_invalid_error=PhoneNumberInvalidError,
    )


def create_telethon_client(
    *,
    api_id: int,
    api_hash: str,
    session_string: str | None,
) -> TelethonClientLike:
    """Build one Telethon client from a StringSession."""

    imported = import_telethon()
    session = imported.string_session_cls(session_string or "")
    client = imported.telegram_client_cls(
        session,
        api_id,
        api_hash,
        device_model="AFKBOT",
        app_version="afkbot-telethon",
    )
    return cast(TelethonClientLike, client)
