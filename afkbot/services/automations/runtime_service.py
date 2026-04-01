"""Runtime helpers for webhook/cron automation execution from CLI/API adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.automations.loop_factory import AgentLoopLike
from afkbot.services.automations.service import AutomationsServiceError, get_automations_service
from afkbot.settings import Settings, get_settings

if TYPE_CHECKING:
    from afkbot.services.agent_loop.loop import AgentLoop


async def trigger_webhook_payload(*, token: str, payload_json: str) -> str:
    """Trigger one webhook automation by token and return deterministic JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        payload = _parse_payload_json(payload_json)
        service = get_automations_service(settings)
        result = await service.trigger_webhook(
            token=token,
            payload=payload,
            agent_loop_factory=lambda session, profile_id: cast(
                AgentLoopLike,
                build_runtime_agent_loop(
                    session,
                    profile_id=profile_id,
                    settings=settings,
                ),
            ),
        )
        return json.dumps({"webhook_trigger": result.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    except ValueError as exc:
        return _error_json(error_code="invalid_payload_json", reason=str(exc))
    finally:
        await engine.dispose()


async def tick_cron_payload(*, now_utc: datetime | None = None) -> str:
    """Execute one cron tick and return deterministic JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        effective_now = now_utc or datetime.now(timezone.utc)
        service = get_automations_service(settings)
        result = await service.tick_cron(
            now_utc=effective_now,
            agent_loop_factory=lambda session, profile_id: cast(
                AgentLoopLike,
                build_runtime_agent_loop(
                    session,
                    profile_id=profile_id,
                    settings=settings,
                ),
            ),
        )
        return json.dumps({"cron_tick": result.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


def build_runtime_agent_loop(
    session: AsyncSession,
    *,
    profile_id: str,
    settings: Settings | None = None,
) -> "AgentLoop":
    """Build AgentLoop instance used by automation runtime execution paths."""

    from afkbot.services.agent_loop.runtime_factory import build_profile_agent_loop

    effective_settings = settings or get_settings()
    return build_profile_agent_loop(
        session,
        settings=effective_settings,
        profile_id=profile_id,
    )


def _parse_payload_json(payload_json: str) -> Mapping[str, object]:
    stripped = payload_json.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("payload_json must be a valid JSON object") from exc
    try:
        return coerce_webhook_payload_mapping(parsed)
    except ValueError:
        raise ValueError("payload_json must be a JSON object")


def coerce_webhook_payload_mapping(payload: object) -> Mapping[str, object]:
    """Normalize webhook payload into string-key mapping accepted by service."""

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return {str(key): value for key, value in payload.items()}


def _error_json(*, error_code: str, reason: str) -> str:
    return json.dumps({"ok": False, "error_code": error_code, "reason": reason}, ensure_ascii=True)
