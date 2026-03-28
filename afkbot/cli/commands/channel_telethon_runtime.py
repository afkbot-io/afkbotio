"""Support helpers for Telethon channel CLI commands."""

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
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.channels.telethon_user import (
    TelethonUserServiceError,
    authorize_telethon_endpoint,
    list_telethon_dialogs,
    logout_telethon_endpoint,
    probe_telethon_endpoint,
)
from afkbot.services.channels.telethon_user.runtime_support import evaluate_telethon_profile_policy
from afkbot.services.health import TelethonUserEndpointReport, run_channel_health_diagnostics
from afkbot.settings import get_settings


async def load_telethon_endpoint(*, channel_id: str) -> TelethonUserEndpointConfig:
    """Load one endpoint and ensure it belongs to the Telethon transport family."""

    settings = get_settings()
    endpoint = await get_channel_endpoint_service(settings).get(endpoint_id=channel_id)
    if endpoint.transport != "telegram_user" or endpoint.adapter_kind != "telethon_userbot":
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_type_mismatch",
            reason=f"Channel endpoint `{channel_id}` is not a Telethon user channel.",
        )
    return TelethonUserEndpointConfig.model_validate(endpoint.model_dump())


def set_telethon_endpoint_enabled(*, channel_id: str, enabled: bool) -> None:
    """Enable or disable one Telethon endpoint and matching binding when present."""

    try:
        updated = asyncio.run(_set_telethon_endpoint_enabled(channel_id=channel_id, enabled=enabled))
    except Exception as exc:
        raise_telethon_channel_error(exc)
    typer.echo(f"Telethon channel `{updated.endpoint_id}` enabled={updated.enabled}.")
    reload_install_managed_runtime_notice(get_settings())


async def _set_telethon_endpoint_enabled(
    *,
    channel_id: str,
    enabled: bool,
) -> TelethonUserEndpointConfig:
    """Enable or disable one Telethon endpoint and matching binding in one loop."""

    settings = get_settings()
    service = get_channel_endpoint_service(settings)
    current = await load_telethon_endpoint(channel_id=channel_id)
    updated = TelethonUserEndpointConfig.model_validate(
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


def raise_telethon_channel_error(exc: Exception) -> None:
    """Map structured Telethon exceptions into CLI usage errors."""

    if isinstance(exc, ChannelEndpointServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    if isinstance(exc, ChannelBindingServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    if isinstance(exc, TelethonUserServiceError):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    raise_usage_error(str(exc))


async def telethon_status_payload(
    *,
    channel_id: str | None,
    probe: bool,
) -> dict[str, object]:
    """Build Telethon endpoint status payload with optional live session probe."""

    settings = get_settings()
    report = await run_channel_health_diagnostics(settings)
    endpoints = list(report.telethon_userbot)
    if channel_id is not None:
        await load_telethon_endpoint(channel_id=channel_id)
        endpoints = [item for item in endpoints if item.endpoint_id == channel_id]
        if not endpoints:
            raise ChannelEndpointServiceError(
                error_code="channel_endpoint_not_found",
                reason=f"Channel endpoint not found: {channel_id}",
            )
    serialized_endpoints: list[dict[str, object]] = [
        serialize_telethon_endpoint_report(item) for item in endpoints
    ]
    payload: dict[str, object] = {
        "ok": True,
        "telethon_userbot": serialized_endpoints,
    }
    for item in serialized_endpoints:
        allowed, reason = await evaluate_telethon_profile_policy(
            settings=settings,
            profile_id=str(item["profile_id"]),
        )
        item["policy_allows_runtime"] = allowed
        item["policy_reason"] = reason
    if not probe:
        return payload
    endpoint_service = get_channel_endpoint_service(settings)
    probe_results: list[dict[str, object]] = []
    for endpoint_report in endpoints:
        try:
            endpoint = await endpoint_service.get(endpoint_id=endpoint_report.endpoint_id)
            identity = await probe_telethon_endpoint(
                settings=settings,
                endpoint=TelethonUserEndpointConfig.model_validate(endpoint.model_dump()),
            )
        except Exception as exc:
            payload["ok"] = False
            probe_results.append(
                {
                    "endpoint_id": endpoint_report.endpoint_id,
                    **telethon_error_payload(exc),
                }
            )
            continue
        probe_results.append(
            {
                "endpoint_id": endpoint_report.endpoint_id,
                "ok": True,
                "user_id": identity.user_id,
                "username": identity.username,
                "phone": identity.phone,
                "display_name": identity.display_name,
            }
        )
    payload["probe"] = probe_results
    return payload


async def telethon_authorize_payload(*, channel_id: str, qr: bool) -> dict[str, object]:
    """Authorize one Telethon endpoint interactively and return CLI payload."""

    settings = get_settings()
    endpoint = await load_telethon_endpoint(channel_id=channel_id)
    result = await authorize_telethon_endpoint(
        settings=settings,
        endpoint=endpoint,
        prompt=_prompt_value,
        notify=typer.echo,
        qr=qr,
    )
    return {
        "ok": True,
        "endpoint_id": channel_id,
        "user_id": result.user_id,
        "username": result.username,
        "phone": result.phone,
        "session_string_saved": result.session_string_saved,
        "method": result.method,
    }


async def telethon_logout_payload(*, channel_id: str) -> dict[str, object]:
    """Logout one Telethon endpoint and clear local state."""

    settings = get_settings()
    endpoint = await load_telethon_endpoint(channel_id=channel_id)
    result = await logout_telethon_endpoint(
        settings=settings,
        endpoint=endpoint,
    )
    return {"ok": True, "endpoint_id": channel_id, **result}


async def telethon_reset_state_payload(*, channel_id: str) -> dict[str, object]:
    """Delete saved Telethon runtime state file for one endpoint."""

    settings = get_settings()
    endpoint = await load_telethon_endpoint(channel_id=channel_id)
    endpoint_service = get_channel_endpoint_service(settings)
    state_path = endpoint_service.telethon_user_state_path(endpoint_id=endpoint.endpoint_id)
    removed = False
    if state_path.exists():
        state_path.unlink()
        removed = True
    return {
        "ok": True,
        "endpoint_id": channel_id,
        "removed": removed,
        "state_path": str(state_path),
    }


async def telethon_dialogs_payload(
    *,
    channel_id: str,
    query: str | None,
    watched_only: bool,
    limit: int,
) -> dict[str, object]:
    """Build read-only dialog discovery payload for one Telethon endpoint."""

    settings = get_settings()
    endpoint = await load_telethon_endpoint(channel_id=channel_id)
    dialogs = await list_telethon_dialogs(
        settings=settings,
        endpoint=endpoint,
        query=query,
        watched_only=watched_only,
        limit=limit,
    )
    return {
        "ok": True,
        "endpoint_id": channel_id,
        "query": query,
        "watched_only": watched_only,
        "dialogs": [item.to_payload() for item in dialogs],
    }


def telethon_error_payload(exc: Exception) -> dict[str, object]:
    """Convert Telethon adapter exception into stable CLI payload."""

    if isinstance(exc, TelethonUserServiceError):
        return {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
    if isinstance(exc, ChannelEndpointServiceError):
        return {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
    return {"ok": False, "error_code": "telethon_channel_error", "reason": str(exc)}


def serialize_telethon_endpoint_report(report: TelethonUserEndpointReport) -> dict[str, object]:
    """Serialize one Telethon health report into CLI/API payload."""

    return {
        "endpoint_id": report.endpoint_id,
        "enabled": report.enabled,
        "profile_id": report.profile_id,
        "credential_profile_key": report.credential_profile_key,
        "account_id": report.account_id,
        "profile_valid": report.profile_valid,
        "profile_exists": report.profile_exists,
        "api_id_configured": report.api_id_configured,
        "api_hash_configured": report.api_hash_configured,
        "phone_configured": report.phone_configured,
        "session_string_configured": report.session_string_configured,
        "policy_allows_runtime": report.policy_allows_runtime,
        "binding_count": report.binding_count,
        "state_path": report.state_path,
        "state_present": report.state_present,
    }


def render_telethon_status_payload(payload: dict[str, object]) -> None:
    """Render human-readable `afk channel telethon status` output."""

    endpoints = payload.get("telethon_userbot")
    if not isinstance(endpoints, list) or not endpoints:
        typer.echo("No Telethon channels configured.")
        return
    typer.echo(f"Telethon userbot endpoints: {len(endpoints)}")
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        typer.echo(
            f"- {item['endpoint_id']}: enabled={item['enabled']}, profile={item['profile_id']}, "
            f"credential_profile={item['credential_profile_key']}, account_id={item['account_id']}, "
            f"api_id_configured={item['api_id_configured']}, api_hash_configured={item['api_hash_configured']}, "
            f"session_string_configured={item['session_string_configured']}, "
            f"policy_allows_runtime={item['policy_allows_runtime']}, "
            f"binding_count={item['binding_count']}, state_present={item['state_present']}"
        )
        if item.get("policy_reason"):
            typer.echo(f"  policy_reason: {item['policy_reason']}")
    probe = payload.get("probe")
    if isinstance(probe, list):
        typer.echo("Live probe:")
        for item in probe:
            if not isinstance(item, dict):
                continue
            if item.get("ok") is True:
                typer.echo(
                    f"- {item['endpoint_id']}: ok user_id={item['user_id']} "
                    f"username={item['username']} phone={item['phone']}"
                )
            else:
                typer.echo(
                    f"- {item['endpoint_id']}: ERROR [{item.get('error_code')}] {item.get('reason')}"
                )


def _prompt_value(label: str, hide_input: bool) -> str:
    return str(typer.prompt(label, hide_input=hide_input))
