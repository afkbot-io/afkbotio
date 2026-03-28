"""Telethon user-channel contracts and credential helpers."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.settings import Settings

TELETHON_CREDENTIAL_API_ID = "api_id"
TELETHON_CREDENTIAL_API_HASH = "api_hash"
TELETHON_CREDENTIAL_SESSION_STRING = "session_string"
TELETHON_CREDENTIAL_PHONE = "phone"
_TELETHON_APP_TOOL_NAME = "app.run"


@dataclass(frozen=True, slots=True)
class TelethonResolvedCredentials:
    """Resolved Telethon runtime credentials."""

    api_id: int
    api_hash: str
    session_string: str | None
    phone: str | None


async def resolve_telethon_credentials(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    require_session: bool,
) -> TelethonResolvedCredentials:
    """Resolve Telethon credentials from the shared credentials store."""

    api_id_raw = await _resolve_secret(
        settings=settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_API_ID,
        required=True,
    )
    api_hash = await _resolve_secret(
        settings=settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_API_HASH,
        required=True,
    )
    session_string = await _resolve_secret(
        settings=settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_SESSION_STRING,
        required=require_session,
    )
    phone = await _resolve_secret(
        settings=settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_PHONE,
        required=False,
    )
    if api_id_raw is None:
        raise TelethonUserServiceError(
            error_code="telethon_missing_api_id",
            reason=(
                f"Missing Telethon credential `{TELETHON_CREDENTIAL_API_ID}` "
                f"in credential profile `{credential_profile_key}`."
            ),
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise TelethonUserServiceError(
            error_code="telethon_invalid_api_id",
            reason="Configured Telethon api_id must be an integer.",
        ) from exc
    if api_id <= 0:
        raise TelethonUserServiceError(
            error_code="telethon_invalid_api_id",
            reason="Configured Telethon api_id must be > 0.",
        )
    if api_hash is None:
        raise TelethonUserServiceError(
            error_code="telethon_missing_api_hash",
            reason=(
                f"Missing Telethon credential `{TELETHON_CREDENTIAL_API_HASH}` "
                f"in credential profile `{credential_profile_key}`."
            ),
        )
    return TelethonResolvedCredentials(
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        phone=phone,
    )


async def upsert_telethon_secret(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str,
) -> None:
    """Create or update one Telethon credential binding."""

    service = get_credentials_service(settings)
    normalized = secret_value.strip()
    if not normalized:
        raise TelethonUserServiceError(
            error_code="telethon_secret_value_required",
            reason=f"Secret value is required for {credential_name}.",
        )
    try:
        try:
            await service.create(
                profile_id=profile_id,
                tool_name=_TELETHON_APP_TOOL_NAME,
                integration_name="telethon",
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                secret_value=normalized,
                replace_existing=False,
            )
        except CredentialsServiceError as exc:
            if exc.error_code != "credentials_conflict":
                raise
            await service.update(
                profile_id=profile_id,
                tool_name=_TELETHON_APP_TOOL_NAME,
                integration_name="telethon",
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                secret_value=normalized,
            )
    except CredentialsServiceError as exc:
        raise TelethonUserServiceError(error_code=exc.error_code, reason=exc.reason) from exc


async def delete_telethon_secret(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    credential_name: str,
) -> bool:
    """Delete one Telethon credential binding when present."""

    try:
        return await get_credentials_service(settings).delete(
            profile_id=profile_id,
            tool_name=_TELETHON_APP_TOOL_NAME,
            integration_name="telethon",
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
        )
    except CredentialsServiceError as exc:
        if exc.error_code == "credentials_not_found":
            return False
        raise TelethonUserServiceError(error_code=exc.error_code, reason=exc.reason) from exc


async def _resolve_secret(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    credential_name: str,
    required: bool,
) -> str | None:
    try:
        value = await get_credentials_service(settings).resolve_plaintext_for_app_tool(
            profile_id=profile_id,
            tool_name=_TELETHON_APP_TOOL_NAME,
            integration_name="telethon",
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
        )
    except CredentialsServiceError as exc:
        if not required and exc.error_code in {
            "credentials_missing",
            "credentials_not_found",
            "credential_profile_required",
        }:
            return None
        if exc.error_code in {
            "credentials_missing",
            "credentials_not_found",
        }:
            raise TelethonUserServiceError(
                error_code=f"telethon_missing_{credential_name}",
                reason=(
                    f"Missing Telethon credential `{credential_name}` "
                    f"in credential profile `{credential_profile_key}`."
                ),
            ) from exc
        raise TelethonUserServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    normalized = value.strip()
    if not normalized:
        if required:
            raise TelethonUserServiceError(
                error_code=f"telethon_missing_{credential_name}",
                reason=(
                    f"Missing Telethon credential `{credential_name}` "
                    f"in credential profile `{credential_profile_key}`."
                ),
            )
        return None
    return normalized
