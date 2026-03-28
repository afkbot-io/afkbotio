"""IMAP app actions for unified `app.run` runtime."""

from __future__ import annotations

import asyncio
import imaplib
import ssl
from email import policy
from email.parser import BytesParser

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from afkbot.services.apps.common import (
    AppCallContext,
    credentials_error_result,
    ensure_host_allowed,
    policy_error_result,
    resolve_credential_value,
    resolve_optional_bool_credential,
)
from afkbot.services.apps.credential_manifest import (
    ActionCredentialManifest,
    AppCredentialManifest,
    CredentialFieldManifest,
)
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.params_validation import build_app_params_validation_error
from afkbot.services.apps.registry import register_app
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.policy import PolicyViolationError
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_ALLOWED_ACTIONS = frozenset({"search_messages"})
_ALLOWED_SKILLS = frozenset({"imap"})
_CREDENTIAL_MANIFEST = AppCredentialManifest(
    fields={
        "imap_host": CredentialFieldManifest(slug="imap_host", description="IMAP server host."),
        "imap_port": CredentialFieldManifest(slug="imap_port", description="IMAP server port."),
        "imap_username": CredentialFieldManifest(
            slug="imap_username",
            description="IMAP account username.",
        ),
        "imap_password": CredentialFieldManifest(
            slug="imap_password",
            description="IMAP account password or app password.",
        ),
        "imap_use_ssl": CredentialFieldManifest(
            slug="imap_use_ssl",
            description="Enable IMAP SSL.",
            secret=False,
            required_by_default=False,
        ),
    },
    actions={
        "search_messages": ActionCredentialManifest(
            required=("imap_host", "imap_port", "imap_username", "imap_password"),
            optional=("imap_use_ssl",),
        ),
    },
)


class _SearchMessagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="ALL", min_length=1, max_length=256)
    mailbox: str = Field(default="INBOX", min_length=1, max_length=128)
    limit: int = Field(default=10, ge=1, le=100)
    host_credential_name: str = Field(default="imap_host", min_length=1, max_length=128)
    port_credential_name: str = Field(default="imap_port", min_length=1, max_length=128)
    username_credential_name: str = Field(default="imap_username", min_length=1, max_length=128)
    password_credential_name: str = Field(default="imap_password", min_length=1, max_length=128)
    use_ssl_credential_name: str = Field(default="imap_use_ssl", min_length=1, max_length=128)


_ACTION_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "search_messages": _SearchMessagesParams,
}


@register_app(
    name="imap",
    allowed_skills=_ALLOWED_SKILLS,
    allowed_actions=_ALLOWED_ACTIONS,
    action_params_models=_ACTION_PARAMS_MODELS,
    credential_manifest=_CREDENTIAL_MANIFEST,
)
async def run_imap_action(
    settings: Settings,
    ctx: AppRuntimeContext,
    action: str,
    params: dict[str, object],
) -> ToolResult:
    """Dispatch IMAP app action by name."""

    normalized_action = action.strip().lower()
    if normalized_action != "search_messages":
        return ToolResult.error(
            error_code="app_action_not_supported",
            reason=f"Unsupported imap action: {action}",
        )

    call_context = AppCallContext(
        profile_id=ctx.profile_id,
        app_name="imap",
        action=normalized_action,
        profile_name=ctx.credential_profile_key,
    )

    try:
        payload = _SearchMessagesParams.model_validate(params)
        host = await resolve_credential_value(
            settings=settings,
            context=call_context,
            credential_slug=payload.host_credential_name,
        )
        port_raw = await resolve_credential_value(
            settings=settings,
            context=call_context,
            credential_slug=payload.port_credential_name,
        )
        username = await resolve_credential_value(
            settings=settings,
            context=call_context,
            credential_slug=payload.username_credential_name,
        )
        password = await resolve_credential_value(
            settings=settings,
            context=call_context,
            credential_slug=payload.password_credential_name,
        )
        use_ssl = await resolve_optional_bool_credential(
            settings=settings,
            context=call_context,
            credential_slug=payload.use_ssl_credential_name,
            default=True,
        )
        host_normalized = host.strip()
        port = _parse_port(port_raw, label="IMAP")
        await ensure_host_allowed(
            settings=settings,
            context=call_context,
            host=host_normalized,
        )

        messages = await _search_messages(
            host=host_normalized,
            port=port,
            username=username,
            password=password,
            mailbox=payload.mailbox,
            query=payload.query,
            limit=payload.limit,
            use_ssl=use_ssl,
            timeout_sec=ctx.timeout_sec,
        )
        return ToolResult(ok=True, payload={"ok": True, "action": "search_messages", "messages": messages, "count": len(messages)})
    except CredentialsServiceError as exc:
        error_code, reason, metadata = credentials_error_result(exc=exc, context=call_context)
        return ToolResult.error(error_code=error_code, reason=reason, metadata=metadata)
    except PolicyViolationError as exc:
        error_code, reason = policy_error_result(exc)
        return ToolResult.error(error_code=error_code, reason=reason)
    except ValidationError as exc:
        return build_app_params_validation_error(
            app_name="imap",
            action=normalized_action,
            model=_SearchMessagesParams,
            exc=exc,
        )
    except ValueError as exc:
        return ToolResult.error(error_code="app_run_invalid", reason=str(exc))
    except TimeoutError:
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"IMAP action timed out after {ctx.timeout_sec} seconds",
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"{exc.__class__.__name__}: {exc}",
        )
def _parse_port(raw_value: str, *, label: str) -> int:
    candidate = raw_value.strip()
    if not candidate:
        raise ValueError(f"{label} port is empty")
    try:
        return int(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} port must be an integer") from exc


async def _search_messages(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    mailbox: str,
    query: str,
    limit: int,
    use_ssl: bool,
    timeout_sec: int,
) -> list[dict[str, object]]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _search_messages_sync,
            host=host,
            port=port,
            username=username,
            password=password,
            mailbox=mailbox,
            query=query,
            limit=limit,
            use_ssl=use_ssl,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


def _search_messages_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    mailbox: str,
    query: str,
    limit: int,
    use_ssl: bool,
    timeout_sec: int,
) -> list[dict[str, object]]:
    if not host:
        raise ValueError("IMAP host is empty")
    if port <= 0:
        raise ValueError("IMAP port must be positive")
    tls_context = ssl.create_default_context()

    if use_ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            host=host,
            port=port,
            timeout=float(timeout_sec),
            ssl_context=tls_context,
        )
    else:
        client = imaplib.IMAP4(host=host, port=port, timeout=float(timeout_sec))

    with client:
        login_code, _ = client.login(username, password)
        if login_code != "OK":
            raise RuntimeError("IMAP login failed")
        select_code, _ = client.select(mailbox)
        if select_code != "OK":
            raise RuntimeError(f"Cannot select mailbox: {mailbox}")

        search_code, search_data = client.search(None, query)
        if search_code != "OK" or not search_data:
            return []
        ids_raw = search_data[0].decode("utf-8", errors="ignore").strip()
        if not ids_raw:
            return []
        ids = ids_raw.split()
        selected_ids = ids[-limit:]
        messages: list[dict[str, object]] = []

        for message_id in selected_ids:
            fetch_code, fetch_data = client.fetch(message_id, "(RFC822)")
            if fetch_code != "OK" or not fetch_data:
                continue
            message_bytes: bytes | None = None
            for part in fetch_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                    message_bytes = part[1]
                    break
            if message_bytes is None:
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(message_bytes)
            messages.append(
                {
                    "id": message_id,
                    "subject": str(parsed.get("Subject", "")),
                    "from": str(parsed.get("From", "")),
                    "date": str(parsed.get("Date", "")),
                }
            )
        return messages
