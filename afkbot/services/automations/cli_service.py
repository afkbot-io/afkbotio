"""Service-layer helpers for automation CLI payload generation."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.service import AutomationsServiceError, get_automations_service
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.settings import get_settings


async def create_automation_payload(
    *,
    profile_id: str,
    name: str,
    prompt: str,
    trigger_type: str,
    cron_expr: str | None,
    timezone_name: str,
    delivery_mode: str | None = None,
    delivery_target: ChannelDeliveryTarget | None = None,
) -> str:
    """Create one automation and return deterministic JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_automations_service(settings)
        normalized_type = trigger_type.strip().lower()
        if normalized_type == "cron":
            if cron_expr is None or not cron_expr.strip():
                return _error_json(
                    error_code="invalid_cron_expr",
                    reason="--cron-expr is required for cron trigger",
                )
            item = await service.create_cron(
                profile_id=profile_id,
                name=name,
                prompt=prompt,
                cron_expr=cron_expr,
                timezone_name=timezone_name,
                delivery_mode=delivery_mode,
                delivery_target=delivery_target,
            )
            return json.dumps({"automation": item.model_dump(mode="json")}, ensure_ascii=True)
        if normalized_type == "webhook":
            item = await service.create_webhook(
                profile_id=profile_id,
                name=name,
                prompt=prompt,
                delivery_mode=delivery_mode,
                delivery_target=delivery_target,
            )
            return json.dumps({"automation": item.model_dump(mode="json")}, ensure_ascii=True)
        return _error_json(
            error_code="invalid_trigger_type",
            reason=f"Unsupported trigger_type: {trigger_type}",
        )
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def list_automations_payload(*, profile_id: str, include_deleted: bool = False) -> str:
    """List automation metadata and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_automations_service(settings)
        items = await service.list(profile_id=profile_id, include_deleted=include_deleted)
        return json.dumps(
            {"automations": [item.model_dump(mode="json") for item in items]},
            ensure_ascii=True,
        )
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def get_automation_payload(*, profile_id: str, automation_id: int) -> str:
    """Get automation metadata and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_automations_service(settings)
        item = await service.get(profile_id=profile_id, automation_id=automation_id)
        return json.dumps({"automation": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def update_automation_payload(
    *,
    profile_id: str,
    automation_id: int,
    name: str | None,
    prompt: str | None,
    status: str | None,
    cron_expr: str | None,
    timezone_name: str | None,
    rotate_webhook_token: bool,
    delivery_mode: str | None,
    delivery_target: ChannelDeliveryTarget | None,
    clear_delivery_target: bool,
) -> str:
    """Update automation metadata and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_automations_service(settings)
        item = await service.update(
            profile_id=profile_id,
            automation_id=automation_id,
            name=name,
            prompt=prompt,
            status=status,
            cron_expr=cron_expr,
            timezone_name=timezone_name,
            rotate_webhook_token=rotate_webhook_token,
            delivery_mode=delivery_mode,
            delivery_target=delivery_target,
            clear_delivery_target=clear_delivery_target,
        )
        return json.dumps({"automation": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def delete_automation_payload(*, profile_id: str, automation_id: int) -> str:
    """Soft-delete automation and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        await _ensure_profile_exists(session_factory, profile_id)
        service = get_automations_service(settings)
        await service.delete(profile_id=profile_id, automation_id=automation_id)
        return json.dumps({"deleted": True, "id": automation_id}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def _ensure_profile_exists(
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
) -> None:
    async with session_scope(session_factory) as session:
        profile = await ProfileRepository(session).get(profile_id)
        if profile is None:
            raise AutomationsServiceError(
                error_code="profile_not_found",
                reason="Profile not found",
            )


def _error_json(*, error_code: str, reason: str) -> str:
    return json.dumps(
        {"ok": False, "error_code": error_code, "reason": reason},
        ensure_ascii=True,
    )
