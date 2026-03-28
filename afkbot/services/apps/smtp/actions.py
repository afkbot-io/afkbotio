"""SMTP app actions for unified `app.run` runtime."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

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

_ALLOWED_ACTIONS = frozenset({"send_email"})
_ALLOWED_SKILLS = frozenset({"smtp"})
_CREDENTIAL_MANIFEST = AppCredentialManifest(
    fields={
        "smtp_host": CredentialFieldManifest(slug="smtp_host", description="SMTP server host."),
        "smtp_port": CredentialFieldManifest(slug="smtp_port", description="SMTP server port."),
        "smtp_username": CredentialFieldManifest(
            slug="smtp_username",
            description="SMTP account username.",
        ),
        "smtp_password": CredentialFieldManifest(
            slug="smtp_password",
            description="SMTP account password or app password.",
        ),
        "smtp_from_email": CredentialFieldManifest(
            slug="smtp_from_email",
            description="Default From email address.",
        ),
        "smtp_use_tls": CredentialFieldManifest(
            slug="smtp_use_tls",
            description="Enable STARTTLS.",
            secret=False,
            required_by_default=False,
        ),
        "smtp_use_ssl": CredentialFieldManifest(
            slug="smtp_use_ssl",
            description="Enable implicit SSL/TLS.",
            secret=False,
            required_by_default=False,
        ),
    },
    actions={
        "send_email": ActionCredentialManifest(
            required=(
                "smtp_host",
                "smtp_port",
                "smtp_username",
                "smtp_password",
                "smtp_from_email",
            ),
            optional=("smtp_use_tls", "smtp_use_ssl"),
        ),
    },
)


class _SendEmailParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_email: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=200_000)
    content_type: str = Field(default="plain", pattern="^(plain|html)$")
    host_credential_name: str = Field(default="smtp_host", min_length=1, max_length=128)
    port_credential_name: str = Field(default="smtp_port", min_length=1, max_length=128)
    username_credential_name: str = Field(default="smtp_username", min_length=1, max_length=128)
    password_credential_name: str = Field(default="smtp_password", min_length=1, max_length=128)
    from_email_credential_name: str = Field(default="smtp_from_email", min_length=1, max_length=128)
    use_tls_credential_name: str = Field(default="smtp_use_tls", min_length=1, max_length=128)
    use_ssl_credential_name: str = Field(default="smtp_use_ssl", min_length=1, max_length=128)


_ACTION_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "send_email": _SendEmailParams,
}


@register_app(
    name="smtp",
    allowed_skills=_ALLOWED_SKILLS,
    allowed_actions=_ALLOWED_ACTIONS,
    action_params_models=_ACTION_PARAMS_MODELS,
    credential_manifest=_CREDENTIAL_MANIFEST,
)
async def run_smtp_action(
    settings: Settings,
    ctx: AppRuntimeContext,
    action: str,
    params: dict[str, object],
) -> ToolResult:
    """Dispatch SMTP app action by name."""

    normalized_action = action.strip().lower()
    if normalized_action != "send_email":
        return ToolResult.error(
            error_code="app_action_not_supported",
            reason=f"Unsupported smtp action: {action}",
        )

    call_context = AppCallContext(
        profile_id=ctx.profile_id,
        app_name="smtp",
        action=normalized_action,
        profile_name=ctx.credential_profile_key,
    )

    try:
        payload = _SendEmailParams.model_validate(params)
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
        from_email = await resolve_credential_value(
            settings=settings,
            context=call_context,
            credential_slug=payload.from_email_credential_name,
        )
        use_tls = await resolve_optional_bool_credential(
            settings=settings,
            context=call_context,
            credential_slug=payload.use_tls_credential_name,
            default=True,
        )
        use_ssl = await resolve_optional_bool_credential(
            settings=settings,
            context=call_context,
            credential_slug=payload.use_ssl_credential_name,
            default=False,
        )

        host_normalized = host.strip()
        port = _parse_port(port_raw, label="SMTP")
        await ensure_host_allowed(
            settings=settings,
            context=call_context,
            host=host_normalized,
        )
        await _send_email(
            host=host_normalized,
            port=port,
            username=username,
            password=password,
            from_email=from_email,
            to_email=payload.to_email,
            subject=payload.subject,
            body=payload.body,
            content_type=payload.content_type,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout_sec=ctx.timeout_sec,
        )
        return ToolResult(ok=True, payload={"ok": True, "action": "send_email", "to_email": payload.to_email})
    except CredentialsServiceError as exc:
        error_code, reason, metadata = credentials_error_result(exc=exc, context=call_context)
        return ToolResult.error(error_code=error_code, reason=reason, metadata=metadata)
    except PolicyViolationError as exc:
        error_code, reason = policy_error_result(exc)
        return ToolResult.error(error_code=error_code, reason=reason)
    except ValidationError as exc:
        return build_app_params_validation_error(
            app_name="smtp",
            action=normalized_action,
            model=_SendEmailParams,
            exc=exc,
        )
    except ValueError as exc:
        return ToolResult.error(error_code="app_run_invalid", reason=str(exc))
    except TimeoutError:
        return ToolResult.error(
            error_code="app_run_failed",
            reason=f"SMTP action timed out after {ctx.timeout_sec} seconds",
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


async def _send_email(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    content_type: str,
    use_tls: bool,
    use_ssl: bool,
    timeout_sec: int,
) -> None:
    await asyncio.wait_for(
        asyncio.to_thread(
            _send_email_sync,
            host=host,
            port=port,
            username=username,
            password=password,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            body=body,
            content_type=content_type,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


def _send_email_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    content_type: str,
    use_tls: bool,
    use_ssl: bool,
    timeout_sec: int,
) -> None:
    if not host:
        raise ValueError("SMTP host is empty")
    if port <= 0:
        raise ValueError("SMTP port must be positive")

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    subtype = "html" if content_type == "html" else "plain"
    message.set_content(body, subtype=subtype)
    tls_context = ssl.create_default_context()

    if use_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            host=host,
            port=port,
            timeout=float(timeout_sec),
            context=tls_context,
        )
    else:
        client = smtplib.SMTP(host=host, port=port, timeout=float(timeout_sec))

    with client:
        if use_tls and not use_ssl:
            client.starttls(context=tls_context)
        client.login(username, password)
        client.send_message(message)
