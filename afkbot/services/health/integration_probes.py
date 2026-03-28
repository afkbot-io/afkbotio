"""Live integration probe implementations used by doctor matrix."""

from __future__ import annotations

import asyncio
import imaplib
import json
import smtplib
import ssl
from urllib.request import Request, urlopen

from afkbot.services.credentials import CredentialsService, CredentialsServiceError
from afkbot.services.health.contracts import IntegrationSpec
from afkbot.services.health.runtime_support import ensure_host_allowed
from afkbot.services.llm.contracts import LLMMessage, LLMRequest
from afkbot.services.llm.provider import build_llm_provider
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


class IntegrationProbeError(RuntimeError):
    """Structured integration probe failure that keeps one stable error code."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


async def probe_integration(
    *,
    settings: Settings,
    service: CredentialsService | None,
    profile_id: str,
    credential_profile_key: str,
    spec: IntegrationSpec,
) -> None:
    """Run one probe for integration spec."""

    if spec.integration == "llm":
        await probe_llm(
            settings=settings,
            profile_id=profile_id,
        )
        return
    if spec.integration == "http":
        await probe_http(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        return

    assert service is not None
    if spec.integration == "telegram":
        await probe_telegram(
            settings=settings,
            service=service,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        return
    if spec.integration == "imap":
        await probe_imap(
            settings=settings,
            service=service,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        return
    if spec.integration == "smtp":
        await probe_smtp(
            settings=settings,
            service=service,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        return
    raise ValueError(f"Unsupported integration: {spec.integration}")


async def probe_http(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
) -> None:
    """Probe `http.request` integration via GET to example.com."""

    registry = ToolRegistry.from_settings(settings)
    tool = registry.get("http.request")
    if tool is None:
        raise RuntimeError("Tool not registered: http.request")
    params = tool.parse_params(
        {
            "profile_key": profile_id,
            "credential_profile_key": credential_profile_key,
            "method": "GET",
            "url": "https://example.com",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(
        ToolContext(profile_id=profile_id, session_id="doctor-matrix", run_id=0),
        params,
    )
    if not result.ok:
        raise RuntimeError(
            f"{result.error_code or 'http_probe_failed'}: {result.reason or 'unknown'}"
        )


async def probe_llm(
    *,
    settings: Settings,
    profile_id: str,
) -> None:
    """Probe the configured LLM provider via one minimal live completion request."""

    provider = build_llm_provider(settings)
    response = await provider.complete(
        LLMRequest(
            profile_id=profile_id,
            session_id="doctor-matrix",
            context="You are a health-check probe. Reply with OK only.",
            history=[LLMMessage(role="user", content="Reply with OK only.")],
            available_tools=(),
            request_timeout_sec=min(15.0, float(settings.llm_request_timeout_sec)),
        )
    )
    if response.error_code is not None:
        raise IntegrationProbeError(
            error_code=response.error_code,
            reason=response.final_message or "LLM probe failed.",
        )
    if response.kind != "final":
        raise IntegrationProbeError(
            error_code="llm_probe_invalid_shape",
            reason="LLM probe returned tool calls for a no-tools request.",
        )
    final_message = (response.final_message or "").strip()
    if not final_message:
        raise IntegrationProbeError(
            error_code="llm_probe_empty_response",
            reason="LLM probe returned an empty final response.",
        )


async def probe_telegram(
    *,
    settings: Settings,
    service: CredentialsService,
    profile_id: str,
    credential_profile_key: str,
) -> None:
    """Probe Telegram token via Bot API `getMe`."""

    token = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="telegram",
        credential_profile_key=credential_profile_key,
        credential_name="telegram_token",
    )
    await ensure_host_allowed(
        settings=settings,
        profile_id=profile_id,
        tool_name="app.run",
        host="api.telegram.org",
    )
    await asyncio.wait_for(
        asyncio.to_thread(telegram_probe_sync, token=token, timeout_sec=10),
        timeout=12.0,
    )


def telegram_probe_sync(*, token: str, timeout_sec: int) -> None:
    """Sync Telegram probe implementation."""

    request = Request(
        url=f"https://api.telegram.org/bot{token}/getMe",
        method="GET",
    )
    with urlopen(request, timeout=float(timeout_sec)) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = (
            str(payload.get("description"))
            if isinstance(payload, dict)
            else "invalid_response"
        )
        raise RuntimeError(f"telegram_probe_failed: {description}")


async def probe_smtp(
    *,
    settings: Settings,
    service: CredentialsService,
    profile_id: str,
    credential_profile_key: str,
) -> None:
    """Probe SMTP credentials via login+noop without sending email."""

    host = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_host",
    )
    port_raw = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_port",
    )
    username = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_username",
    )
    password = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_password",
    )
    use_tls = await resolve_optional_credential_bool(
        service=service,
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_use_tls",
        default=True,
    )
    use_ssl = await resolve_optional_credential_bool(
        service=service,
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="smtp",
        credential_profile_key=credential_profile_key,
        credential_name="smtp_use_ssl",
        default=False,
    )
    await ensure_host_allowed(
        settings=settings,
        profile_id=profile_id,
        tool_name="app.run",
        host=host.strip(),
    )
    await asyncio.wait_for(
        asyncio.to_thread(
            smtp_probe_sync,
            host=host.strip(),
            port=int(port_raw.strip()),
            username=username,
            password=password,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout_sec=10,
        ),
        timeout=12.0,
    )


def smtp_probe_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    use_ssl: bool,
    timeout_sec: int,
) -> None:
    """Sync SMTP probe implementation."""

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
        code, _ = client.noop()
        if int(code) >= 400:
            raise RuntimeError(f"smtp_noop_failed: {code}")


async def probe_imap(
    *,
    settings: Settings,
    service: CredentialsService,
    profile_id: str,
    credential_profile_key: str,
) -> None:
    """Probe IMAP credentials via login/select in read-only path."""

    host = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="imap",
        credential_profile_key=credential_profile_key,
        credential_name="imap_host",
    )
    port_raw = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="imap",
        credential_profile_key=credential_profile_key,
        credential_name="imap_port",
    )
    username = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="imap",
        credential_profile_key=credential_profile_key,
        credential_name="imap_username",
    )
    password = await service.resolve_plaintext_for_app_tool(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="imap",
        credential_profile_key=credential_profile_key,
        credential_name="imap_password",
    )
    use_ssl = await resolve_optional_credential_bool(
        service=service,
        profile_id=profile_id,
        tool_name="app.run",
        integration_name="imap",
        credential_profile_key=credential_profile_key,
        credential_name="imap_use_ssl",
        default=True,
    )
    await ensure_host_allowed(
        settings=settings,
        profile_id=profile_id,
        tool_name="app.run",
        host=host.strip(),
    )
    await asyncio.wait_for(
        asyncio.to_thread(
            imap_probe_sync,
            host=host.strip(),
            port=int(port_raw.strip()),
            username=username,
            password=password,
            use_ssl=use_ssl,
            timeout_sec=10,
        ),
        timeout=12.0,
    )


def imap_probe_sync(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
    timeout_sec: int,
) -> None:
    """Sync IMAP probe implementation."""

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
            raise RuntimeError("imap_login_failed")
        select_code, _ = client.select("INBOX", readonly=True)
        if select_code != "OK":
            raise RuntimeError("imap_select_failed")


async def resolve_optional_credential_bool(
    *,
    service: CredentialsService,
    profile_id: str,
    tool_name: str,
    integration_name: str,
    credential_profile_key: str,
    credential_name: str,
    default: bool,
) -> bool:
    """Resolve optional bool credential with default fallback for missing binding."""

    try:
        raw = await service.resolve_plaintext_for_app_tool(
            profile_id=profile_id,
            tool_name=tool_name,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
        )
    except CredentialsServiceError as exc:
        if exc.error_code == "credentials_missing":
            return default
        raise

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
