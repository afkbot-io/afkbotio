"""Telethon authorization, probe, and logout helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import telethon_user_state_path_for
from afkbot.services.channels.telethon_user.client import (
    TelethonClientLike,
    create_telethon_client,
    import_telethon,
)
from afkbot.services.channels.telethon_user.contracts import (
    TELETHON_CREDENTIAL_PHONE,
    TELETHON_CREDENTIAL_SESSION_STRING,
    delete_telethon_secret,
    resolve_telethon_credentials,
    upsert_telethon_secret,
)
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.runtime_support import (
    persist_telethon_identity_state,
    resolve_telethon_identity,
    validate_telethon_profile_policy,
)
from afkbot.services.channels.telethon_user.normalization import TelethonUserIdentity
from afkbot.services.channels.telethon_user.qr_terminal import (
    describe_qr_expiry,
    render_terminal_qr,
)
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class TelethonProbeIdentity:
    """Live Telethon identity probe payload."""

    user_id: int
    username: str | None
    phone: str | None
    display_name: str | None


@dataclass(frozen=True, slots=True)
class TelethonAuthorizationResult:
    """Successful Telethon login payload returned to CLI."""

    user_id: int
    username: str | None
    phone: str | None
    session_string_saved: bool
    method: str


PromptFn = Callable[[str, bool], str]
NotifyFn = Callable[[str], None]


async def authorize_telethon_endpoint(
    *,
    settings: Settings,
    endpoint: TelethonUserEndpointConfig,
    prompt: PromptFn,
    notify: NotifyFn | None = None,
    qr: bool = False,
) -> TelethonAuthorizationResult:
    """Run interactive Telethon login flow and persist session_string."""

    await validate_telethon_profile_policy(settings=settings, profile_id=endpoint.profile_id)
    credentials = await resolve_telethon_credentials(
        settings=settings,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        require_session=False,
    )
    imported = import_telethon()
    phone = _normalize_telegram_phone(credentials.phone or prompt("Telegram phone", False).strip())
    if not phone:
        raise TelethonUserServiceError(
            error_code="telethon_phone_required",
            reason="Telegram phone number is required for authorization.",
        )
    client = create_telethon_client(
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        session_string=None,
    )
    try:
        await _client_connect(client)
        if qr:
            qr_login = await _client_qr_login(client)
            if notify is not None:
                notify(_render_qr_login_message(qr_login=qr_login))
            try:
                await _client_wait_qr_login(qr_login)
            except TimeoutError as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_qr_expired",
                    reason="Telethon QR login expired before it was confirmed. Run authorize --qr again.",
                ) from exc
            except asyncio.TimeoutError as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_qr_expired",
                    reason="Telethon QR login expired before it was confirmed. Run authorize --qr again.",
                ) from exc
            except imported.session_password_needed_error:
                password = prompt("Telegram 2FA password", True).strip()
                if not password:
                    raise TelethonUserServiceError(
                        error_code="telethon_password_required",
                        reason="Telegram 2FA password is required.",
                    )
                await _client_sign_in(client, password=password)
            except imported.phone_number_invalid_error as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_phone_invalid",
                    reason="Telegram rejected the configured phone number. Check the stored number and try again.",
                ) from exc
        else:
            sent_code = await _client_send_code_request(client, phone=phone)
            if notify is not None:
                notify(_render_sent_code_message(sent_code=sent_code, phone=phone))
            code = prompt("Telegram login code", False).strip()
            if not code:
                raise TelethonUserServiceError(
                    error_code="telethon_code_required",
                    reason="Telegram login code is required.",
                )
            try:
                await _client_sign_in(client, phone=phone, code=code)
            except imported.session_password_needed_error:
                password = prompt("Telegram 2FA password", True).strip()
                if not password:
                    raise TelethonUserServiceError(
                        error_code="telethon_password_required",
                        reason="Telegram 2FA password is required.",
                    )
                await _client_sign_in(client, password=password)
            except imported.phone_code_invalid_error as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_code_invalid",
                    reason="The Telegram login code is invalid. Request a fresh code and try again.",
                ) from exc
            except imported.phone_code_expired_error as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_code_expired",
                    reason="The Telegram login code expired. Request a fresh code and try again.",
                ) from exc
            except imported.phone_number_invalid_error as exc:
                raise TelethonUserServiceError(
                    error_code="telethon_phone_invalid",
                    reason="Telegram rejected the configured phone number. Check the stored number and try again.",
                ) from exc
        identity = resolve_telethon_identity(await _client_get_me(client))
        phone_to_store = _normalize_telegram_phone(identity.phone or phone)
        await _persist_authorized_session(
            settings=settings,
            endpoint=endpoint,
            identity=identity,
            client=client,
            phone=phone_to_store,
        )
        return TelethonAuthorizationResult(
            user_id=identity.user_id,
            username=identity.username,
            phone=identity.phone,
            session_string_saved=True,
            method="qr" if qr else "code",
        )
    finally:
        await _client_disconnect(client)


async def probe_telethon_endpoint(
    *,
    settings: Settings,
    endpoint: TelethonUserEndpointConfig,
) -> TelethonProbeIdentity:
    """Connect to Telegram and return current user identity."""

    await validate_telethon_profile_policy(settings=settings, profile_id=endpoint.profile_id)
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
        await _client_connect(client)
        if not await _client_is_user_authorized(client):
            raise TelethonUserServiceError(
                error_code="telethon_session_unauthorized",
                reason="Stored Telethon session is not authorized anymore.",
            )
        identity = resolve_telethon_identity(await _client_get_me(client))
        return TelethonProbeIdentity(
            user_id=identity.user_id,
            username=identity.username,
            phone=identity.phone,
            display_name=identity.display_name,
        )
    finally:
        await _client_disconnect(client)


async def logout_telethon_endpoint(
    *,
    settings: Settings,
    endpoint: TelethonUserEndpointConfig,
) -> dict[str, object]:
    """Log out the Telethon session when present and clear local state."""

    removed_session = False
    logged_out = False
    network_logout_skipped = False
    try:
        credentials = await resolve_telethon_credentials(
            settings=settings,
            profile_id=endpoint.profile_id,
            credential_profile_key=endpoint.credential_profile_key,
            require_session=True,
        )
    except TelethonUserServiceError as exc:
        if exc.error_code != "telethon_missing_session_string":
            raise
        credentials = None
    if credentials is not None and credentials.session_string:
        try:
            await validate_telethon_profile_policy(settings=settings, profile_id=endpoint.profile_id)
        except TelethonUserServiceError as exc:
            if exc.error_code != "telethon_policy_network_unsupported":
                raise
            network_logout_skipped = True
        else:
            client = create_telethon_client(
                api_id=credentials.api_id,
                api_hash=credentials.api_hash,
                session_string=credentials.session_string,
            )
            try:
                await _client_connect(client)
                await _client_log_out(client)
                logged_out = True
            finally:
                await _client_disconnect(client)
    removed_session = await delete_telethon_secret(
        settings=settings,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_SESSION_STRING,
    )
    state_path = telethon_user_state_path_for(settings, endpoint_id=endpoint.endpoint_id)
    if state_path.exists():
        state_path.unlink()
    return {
        "logged_out": logged_out,
        "network_logout_skipped": network_logout_skipped,
        "session_removed": removed_session,
        "state_path": str(state_path),
        "state_present": state_path.exists(),
    }


async def _client_connect(client: TelethonClientLike) -> None:
    await client.connect()


async def _client_disconnect(client: TelethonClientLike) -> None:
    try:
        await client.disconnect()
    except Exception:
        return


async def _client_is_user_authorized(client: TelethonClientLike) -> bool:
    return bool(await client.is_user_authorized())


async def _client_get_me(client: TelethonClientLike) -> object:
    entity = await client.get_me()
    if entity is None:
        raise TelethonUserServiceError(
            error_code="telethon_identity_missing",
            reason="Telethon returned no current user identity.",
        )
    return entity


async def _client_send_code_request(client: TelethonClientLike, *, phone: str) -> object:
    return await client.send_code_request(phone)


async def _client_qr_login(client: TelethonClientLike) -> object:
    return await client.qr_login()


async def _client_wait_qr_login(qr_login: object) -> None:
    wait = getattr(qr_login, "wait", None)
    if not callable(wait):
        raise TelethonUserServiceError(
            error_code="telethon_qr_login_unsupported",
            reason="Telethon QR login is unavailable in the installed Telethon build.",
        )
    await wait()


async def _client_sign_in(
    client: TelethonClientLike,
    *,
    phone: str | None = None,
    code: str | None = None,
    password: str | None = None,
) -> None:
    kwargs: dict[str, str] = {}
    if phone is not None:
        kwargs["phone"] = phone
    if code is not None:
        kwargs["code"] = code
    if password is not None:
        kwargs["password"] = password
    await client.sign_in(**kwargs)


def _normalize_telegram_phone(raw: str) -> str:
    """Normalize operator-entered phone to a Telethon-friendly +<digits> form."""

    normalized = raw.strip()
    if not normalized:
        return ""
    if normalized.startswith("+") and normalized[1:].isdigit():
        return normalized
    digits = "".join(char for char in normalized if char.isdigit())
    if not digits:
        return normalized
    return f"+{digits}"


def _render_sent_code_message(*, sent_code: object, phone: str) -> str:
    """Render one human-readable message describing where Telegram sent the login code."""

    code_type = _describe_sent_code_type(getattr(sent_code, "type", None))
    next_type = _describe_next_code_type(getattr(sent_code, "next_type", None))
    timeout = getattr(sent_code, "timeout", None)
    parts = [f"Login code requested for {phone}."]
    if code_type == "telegram_app":
        parts.append("Primary delivery: Telegram app chat from Telegram/777000.")
    elif code_type == "sms":
        parts.append("Primary delivery: SMS.")
    elif code_type == "call":
        parts.append("Primary delivery: phone call.")
    elif code_type == "flash_call":
        parts.append("Primary delivery: flash call / missed call.")
    elif code_type == "email":
        parts.append("Primary delivery: email.")
    else:
        parts.append("Primary delivery: Telegram did not specify a clear method.")
    if isinstance(timeout, int) and timeout > 0:
        parts.append(f"Telegram timeout hint: {timeout}s before the next retry path may unlock.")
    if next_type == "sms":
        parts.append("If nothing appears now, wait for the timeout window; Telegram may allow SMS next.")
    elif next_type == "call":
        parts.append("If nothing appears now, wait for the timeout window; Telegram may allow a phone call next.")
    elif next_type == "flash_call":
        parts.append("If nothing appears now, wait for the timeout window; Telegram may allow a flash call next.")
    elif next_type == "telegram_app":
        parts.append("Telegram still prefers in-app delivery for the next attempt too.")
    if code_type == "telegram_app":
        parts.append("Check all active Telegram clients for that account, including archived system chats.")
    else:
        parts.append("If the code still does not arrive, avoid spamming retries; repeated attempts can delay delivery.")
    return " ".join(parts)


def _render_qr_login_message(*, qr_login: object) -> str:
    """Render one human-readable QR login prompt with URL and terminal QR when available."""

    url = str(getattr(qr_login, "url", "")).strip()
    if not url:
        raise TelethonUserServiceError(
            error_code="telethon_qr_url_missing",
            reason="Telethon did not return a QR login URL.",
        )
    parts = [
        "QR login requested for Telethon.",
        "Open Telegram on a device already signed into this account, then go to Settings -> Devices -> Link Desktop Device and scan the QR below.",
    ]
    expiry_hint = describe_qr_expiry(getattr(qr_login, "expires", None))
    if expiry_hint is not None:
        parts.append(f"QR expiry hint: about {expiry_hint}.")
    parts.append(f"QR login URL: {url}")
    rendered_qr = render_terminal_qr(url)
    if rendered_qr is None:
        parts.append("Terminal QR rendering is unavailable; open the URL above or install the qrcode package.")
        return " ".join(parts)
    return " ".join(parts) + "\n\n" + rendered_qr


def _describe_sent_code_type(sent_code_type: object) -> str:
    """Map Telethon sent-code type objects to one small stable label."""

    if sent_code_type is None:
        return "unknown"
    name = sent_code_type.__class__.__name__.lower()
    if "app" in name:
        return "telegram_app"
    if "sms" in name:
        return "sms"
    if "flashcall" in name or "missedcall" in name:
        return "flash_call"
    if "call" in name:
        return "call"
    if "email" in name:
        return "email"
    return "unknown"


def _describe_next_code_type(next_code_type: object) -> str:
    """Map Telethon next delivery type objects to one small stable label."""

    if next_code_type is None:
        return "unknown"
    name = next_code_type.__class__.__name__.lower()
    if "sms" in name:
        return "sms"
    if "flashcall" in name or "missedcall" in name:
        return "flash_call"
    if "call" in name:
        return "call"
    if "app" in name:
        return "telegram_app"
    if "email" in name:
        return "email"
    return "unknown"


async def _persist_authorized_session(
    *,
    settings: Settings,
    endpoint: TelethonUserEndpointConfig,
    identity: TelethonUserIdentity,
    client: TelethonClientLike,
    phone: str,
) -> None:
    """Persist one successful Telethon authorization into secrets and runtime state."""

    session_string = _client_session_string(client)
    if not session_string:
        raise TelethonUserServiceError(
            error_code="telethon_session_export_failed",
            reason="Telethon returned an empty session_string after login.",
        )
    await upsert_telethon_secret(
        settings=settings,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_SESSION_STRING,
        secret_value=session_string,
    )
    await upsert_telethon_secret(
        settings=settings,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        credential_name=TELETHON_CREDENTIAL_PHONE,
        secret_value=phone,
    )
    await persist_telethon_identity_state(
        state_path=telethon_user_state_path_for(settings, endpoint_id=endpoint.endpoint_id),
        account_id=endpoint.account_id,
        identity=identity,
        last_error=None,
    )


async def _client_log_out(client: TelethonClientLike) -> None:
    await client.log_out()


def _client_session_string(client: TelethonClientLike) -> str:
    session = getattr(client, "session", None)
    if session is None:
        return ""
    save = getattr(session, "save", None)
    if not callable(save):
        return ""
    value = save()
    return value.strip() if isinstance(value, str) else ""
