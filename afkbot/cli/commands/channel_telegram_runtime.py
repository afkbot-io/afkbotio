"""Support helpers for Telegram channel CLI commands."""

from __future__ import annotations

import asyncio

import typer

from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.channel_routing import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    get_channel_binding_service,
)
from afkbot.services.channels.endpoint_contracts import TelegramPollingEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
    telegram_polling_state_path_for,
)
from afkbot.services.channels.telegram_polling import (
    TelegramPollingService,
    TelegramPollingServiceError,
)
from afkbot.services.health import TelegramPollingEndpointReport, run_channel_health_diagnostics
from afkbot.settings import get_settings


async def load_telegram_endpoint(*, channel_id: str) -> TelegramPollingEndpointConfig:
    """Load one endpoint and ensure it belongs to the Telegram polling family."""

    settings = get_settings()
    endpoint = await get_channel_endpoint_service(settings).get(endpoint_id=channel_id)
    if endpoint.transport != "telegram" or endpoint.adapter_kind != "telegram_bot_polling":
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_type_mismatch",
            reason=f"Channel endpoint `{channel_id}` is not a Telegram polling channel.",
        )
    return TelegramPollingEndpointConfig.model_validate(endpoint.model_dump())


def set_endpoint_enabled(*, channel_id: str, enabled: bool) -> None:
    """Enable or disable one Telegram endpoint and matching binding when present."""

    try:
        updated = asyncio.run(_set_telegram_endpoint_enabled(channel_id=channel_id, enabled=enabled))
    except Exception as exc:
        raise_channel_error(exc)
    typer.echo(f"Telegram channel `{updated.endpoint_id}` enabled={updated.enabled}.")
    reload_install_managed_runtime_notice(get_settings())


async def _set_telegram_endpoint_enabled(
    *,
    channel_id: str,
    enabled: bool,
) -> TelegramPollingEndpointConfig:
    """Enable or disable one Telegram endpoint and matching binding in one loop."""

    settings = get_settings()
    service = get_channel_endpoint_service(settings)
    current = await load_telegram_endpoint(channel_id=channel_id)
    updated = TelegramPollingEndpointConfig.model_validate(
        (
            await service.update(current.model_copy(update={"enabled": enabled}))
        ).model_dump()
    )
    try:
        binding_service = get_channel_binding_service(settings)
        binding = await binding_service.get(binding_id=channel_id)
        await binding_service.put(
            ChannelBindingRule(
                **(binding.model_dump(mode="python") | {"enabled": enabled})
            )
        )
    except ChannelBindingServiceError:
        pass
    return updated


def raise_channel_error(exc: Exception) -> None:
    """Map structured Telegram channel exceptions into CLI usage errors."""

    if isinstance(exc, ChannelEndpointServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    if isinstance(exc, ChannelBindingServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    if isinstance(exc, TelegramPollingServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    raise_usage_error(str(exc))


async def telegram_status_payload(
    *,
    channel_id: str | None,
    probe: bool,
) -> dict[str, object]:
    """Build Telegram endpoint status payload with optional live Bot API probe."""

    settings = get_settings()
    report = await run_channel_health_diagnostics(settings)
    endpoints = list(report.telegram_polling)
    if channel_id is not None:
        await load_telegram_endpoint(channel_id=channel_id)
        endpoints = [item for item in endpoints if item.endpoint_id == channel_id]
        if not endpoints:
            raise ChannelEndpointServiceError(
                error_code="channel_endpoint_not_found",
                reason=f"Channel endpoint not found: {channel_id}",
            )
    payload: dict[str, object] = {
        "ok": True,
        "telegram_polling": [
            serialize_endpoint_report(item)
            for item in endpoints
        ],
    }
    if not probe:
        return payload
    probe_results: list[dict[str, object]] = []
    for endpoint in endpoints:
        try:
            service = await build_telegram_service(endpoint_id=endpoint.endpoint_id)
            identity = await service.probe_identity()
        except Exception as exc:
            probe_results.append(
                {
                    "endpoint_id": endpoint.endpoint_id,
                    **telegram_error_payload(exc),
                }
            )
            payload["ok"] = False
            continue
        probe_results.append(
            {
                "endpoint_id": endpoint.endpoint_id,
                "ok": True,
                "bot_id": identity.bot_id,
                "username": identity.username,
            }
        )
    payload["probe"] = probe_results
    return payload


async def telegram_poll_once_payload(*, channel_id: str) -> dict[str, object]:
    """Run one Telegram polling iteration and return CLI payload."""

    settings = get_settings()
    service = await build_telegram_service(endpoint_id=channel_id)
    state_path = telegram_polling_state_path_for(settings, endpoint_id=channel_id)
    try:
        processed = await service.poll_once()
    except Exception as exc:
        return {"channel_id": channel_id, **telegram_error_payload(exc)}
    return {
        "ok": True,
        "channel_id": channel_id,
        "processed_updates": processed,
        "state_path": str(state_path),
        "state_present": state_path.exists(),
    }


async def telegram_reset_offset_payload(*, channel_id: str) -> dict[str, object]:
    """Reset saved Telegram polling offset and return CLI payload."""

    settings = get_settings()
    service = await build_telegram_service(endpoint_id=channel_id)
    state_path = telegram_polling_state_path_for(settings, endpoint_id=channel_id)
    removed = await service.reset_saved_offset()
    return {
        "ok": True,
        "channel_id": channel_id,
        "removed": removed,
        "state_path": str(state_path),
    }


async def build_telegram_service(*, endpoint_id: str) -> TelegramPollingService:
    """Construct one Telegram polling adapter for the selected endpoint."""

    settings = get_settings()
    endpoint = await load_telegram_endpoint(channel_id=endpoint_id)
    return TelegramPollingService(
        settings,
        endpoint=endpoint,
        state_path=telegram_polling_state_path_for(settings, endpoint_id=endpoint.endpoint_id),
    )


def telegram_error_payload(exc: Exception) -> dict[str, object]:
    """Convert one Telegram adapter exception into stable CLI payload."""

    if isinstance(exc, TelegramPollingServiceError):
        return {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
    if isinstance(exc, ChannelEndpointServiceError):
        return {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
    return {"ok": False, "error_code": "telegram_channel_error", "reason": str(exc)}


def serialize_endpoint_report(report: TelegramPollingEndpointReport) -> dict[str, object]:
    """Serialize one channel-health report into CLI/API payload."""

    return {
        "endpoint_id": report.endpoint_id,
        "enabled": report.enabled,
        "profile_id": report.profile_id,
        "credential_profile_key": report.credential_profile_key,
        "account_id": report.account_id,
        "profile_valid": report.profile_valid,
        "profile_exists": report.profile_exists,
        "token_configured": report.token_configured,
        "binding_count": report.binding_count,
        "state_path": report.state_path,
        "state_present": report.state_present,
    }


def render_telegram_status_payload(payload: dict[str, object]) -> None:
    """Render human-readable `afk channel telegram status` output."""

    endpoints = payload.get("telegram_polling")
    if not isinstance(endpoints, list) or not endpoints:
        typer.echo("No Telegram channels configured.")
        return
    typer.echo(f"Telegram polling endpoints: {len(endpoints)}")
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        typer.echo(
            f"- {item['endpoint_id']}: enabled={item['enabled']}, profile={item['profile_id']}, "
            f"credential_profile={item['credential_profile_key']}, account_id={item['account_id']}, "
            f"token_configured={item['token_configured']}, binding_count={item['binding_count']}, "
            f"state_present={item['state_present']}"
        )
    probe = payload.get("probe")
    if isinstance(probe, list):
        typer.echo("Live probe:")
        for item in probe:
            if not isinstance(item, dict):
                continue
            if item.get("ok") is True:
                typer.echo(
                    f"- {item['endpoint_id']}: ok bot_id={item['bot_id']} username={item['username']}"
                )
            else:
                typer.echo(
                    f"- {item['endpoint_id']}: ERROR [{item.get('error_code')}] {item.get('reason')}"
                )


def render_poll_once_payload(*, channel_id: str, payload: dict[str, object]) -> None:
    """Render human-readable `afk channel telegram poll-once` output."""

    if payload.get("ok") is False:
        typer.echo(f"ERROR [{payload.get('error_code')}] {payload.get('reason')}")
        return
    typer.echo(
        f"Telegram poll-once `{channel_id}` processed_updates={payload['processed_updates']} "
        f"state_present={payload['state_present']}"
    )
