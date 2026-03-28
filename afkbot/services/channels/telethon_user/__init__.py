"""Telethon user-channel runtime exports."""

from afkbot.services.channels.telethon_user.auth import (
    TelethonAuthorizationResult,
    TelethonProbeIdentity,
    authorize_telethon_endpoint,
    logout_telethon_endpoint,
    probe_telethon_endpoint,
)
from afkbot.services.channels.telethon_user.contracts import (
    TELETHON_CREDENTIAL_API_HASH,
    TELETHON_CREDENTIAL_API_ID,
    TELETHON_CREDENTIAL_PHONE,
    TELETHON_CREDENTIAL_SESSION_STRING,
    TelethonResolvedCredentials,
)
from afkbot.services.channels.telethon_user.discovery import (
    TelethonDialogRecord,
    list_telethon_dialogs,
)
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.service import TelethonUserService

__all__ = [
    "TELETHON_CREDENTIAL_API_HASH",
    "TELETHON_CREDENTIAL_API_ID",
    "TELETHON_CREDENTIAL_PHONE",
    "TELETHON_CREDENTIAL_SESSION_STRING",
    "TelethonAuthorizationResult",
    "TelethonDialogRecord",
    "TelethonProbeIdentity",
    "TelethonResolvedCredentials",
    "TelethonUserService",
    "TelethonUserServiceError",
    "authorize_telethon_endpoint",
    "list_telethon_dialogs",
    "logout_telethon_endpoint",
    "probe_telethon_endpoint",
]
