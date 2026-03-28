"""Operator-facing health and routing diagnostics routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from afkbot.api.chat_auth import ensure_health_diagnostics_scope, require_chat_http_context

from afkbot.services.health import (
    run_channel_health_diagnostics,
    run_channel_delivery_diagnostics,
    run_channel_routing_diagnostics,
)
from afkbot.settings import get_settings

router = APIRouter(prefix="/v1/health", tags=["health"])


@router.get("/routing")
async def get_routing_diagnostics(
    authorization: str | None = Header(None),
    x_afk_session_proof: str | None = Header(default=None),
) -> dict[str, object]:
    """Return current in-memory channel-routing diagnostics for operators."""

    auth_context = await require_chat_http_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
    )
    ensure_health_diagnostics_scope(auth_context)
    report = await run_channel_routing_diagnostics(get_settings())
    return {
        "ok": True,
        "fallback_transports": list(report.fallback_transports),
        "totals": {
            "total": report.diagnostics.total,
            "matched": report.diagnostics.matched,
            "fallback_used": report.diagnostics.fallback_used,
            "no_match": report.diagnostics.no_match,
            "strict_no_match": report.diagnostics.strict_no_match,
        },
        "transports": [
            {
                "transport": item.transport,
                "total": item.total,
                "matched": item.matched,
                "fallback_used": item.fallback_used,
                "no_match": item.no_match,
                "strict_no_match": item.strict_no_match,
            }
            for item in report.diagnostics.transports
        ],
        "recent_events": [
            {
                "transport": item.transport,
                "strict": item.strict,
                "matched": item.matched,
                "no_match": item.no_match,
                "fallback_used": item.fallback_used,
                "account_id": item.account_id,
                "peer_id": item.peer_id,
                "thread_id": item.thread_id,
                "user_id": item.user_id,
                "binding_id": item.binding_id,
                "profile_id": item.profile_id,
                "session_policy": item.session_policy,
                "prompt_overlay_applied": item.prompt_overlay_applied,
            }
            for item in report.diagnostics.recent_events
        ],
    }


@router.get("/delivery")
async def get_delivery_diagnostics(
    authorization: str | None = Header(None),
    x_afk_session_proof: str | None = Header(default=None),
) -> dict[str, object]:
    """Return current in-memory outbound delivery diagnostics for operators."""

    auth_context = await require_chat_http_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
    )
    ensure_health_diagnostics_scope(auth_context)
    report = await run_channel_delivery_diagnostics(get_settings())
    return {
        "ok": True,
        "totals": {
            "total": report.diagnostics.total,
            "succeeded": report.diagnostics.succeeded,
            "failed": report.diagnostics.failed,
        },
        "transports": [
            {
                "transport": item.transport,
                "total": item.total,
                "succeeded": item.succeeded,
                "failed": item.failed,
            }
            for item in report.diagnostics.transports
        ],
        "recent_events": [
            {
                "transport": item.transport,
                "ok": item.ok,
                "error_code": item.error_code,
                "binding_id": item.binding_id,
                "account_id": item.account_id,
                "peer_id": item.peer_id,
                "thread_id": item.thread_id,
                "user_id": item.user_id,
                "address": item.address,
                "subject": item.subject,
            }
            for item in report.diagnostics.recent_events
        ],
    }


@router.get("/channels")
async def get_channel_health(
    authorization: str | None = Header(None),
    x_afk_session_proof: str | None = Header(default=None),
) -> dict[str, object]:
    """Return configured channel adapter status for operators."""

    auth_context = await require_chat_http_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
    )
    ensure_health_diagnostics_scope(auth_context)
    report = await run_channel_health_diagnostics(get_settings())
    return {
        "ok": True,
        "telegram_polling": {
            "total_endpoints": len(report.telegram_polling),
            "enabled_endpoints": sum(1 for item in report.telegram_polling if item.enabled),
            "endpoints": [
                {
                    "endpoint_id": item.endpoint_id,
                    "enabled": item.enabled,
                    "profile_id": item.profile_id,
                    "credential_profile_key": item.credential_profile_key,
                    "account_id": item.account_id,
                    "profile_valid": item.profile_valid,
                    "profile_exists": item.profile_exists,
                    "token_configured": item.token_configured,
                    "binding_count": item.binding_count,
                    "state_path": item.state_path,
                    "state_present": item.state_present,
                }
                for item in report.telegram_polling
            ],
        },
        "telethon_userbot": {
            "total_endpoints": len(report.telethon_userbot),
            "enabled_endpoints": sum(1 for item in report.telethon_userbot if item.enabled),
            "endpoints": [
                {
                    "endpoint_id": item.endpoint_id,
                    "enabled": item.enabled,
                    "profile_id": item.profile_id,
                    "credential_profile_key": item.credential_profile_key,
                    "account_id": item.account_id,
                    "profile_valid": item.profile_valid,
                    "profile_exists": item.profile_exists,
                    "api_id_configured": item.api_id_configured,
                    "api_hash_configured": item.api_hash_configured,
                    "phone_configured": item.phone_configured,
                    "session_string_configured": item.session_string_configured,
                    "policy_allows_runtime": item.policy_allows_runtime,
                    "binding_count": item.binding_count,
                    "state_path": item.state_path,
                    "state_present": item.state_present,
                }
                for item in report.telethon_userbot
            ],
        },
    }
