"""Service-layer helpers for automation CLI payload generation."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.graph.contracts import AutomationGraphSpec
from afkbot.services.automations.service import AutomationsServiceError, get_automations_service
from afkbot.settings import get_settings


async def create_automation_payload(
    *,
    profile_id: str,
    name: str,
    prompt: str,
    trigger_type: str,
    cron_expr: str | None,
    timezone_name: str,
    execution_mode: str,
    graph_fallback_mode: str,
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
                execution_mode=execution_mode,
                graph_fallback_mode=graph_fallback_mode,
            )
            return json.dumps({"automation": item.model_dump(mode="json")}, ensure_ascii=True)
        if normalized_type == "webhook":
            item = await service.create_webhook(
                profile_id=profile_id,
                name=name,
                prompt=prompt,
                execution_mode=execution_mode,
                graph_fallback_mode=graph_fallback_mode,
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
    execution_mode: str | None,
    graph_fallback_mode: str | None,
    cron_expr: str | None,
    timezone_name: str | None,
    rotate_webhook_token: bool,
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
            execution_mode=execution_mode,
            graph_fallback_mode=graph_fallback_mode,
            cron_expr=cron_expr,
            timezone_name=timezone_name,
            rotate_webhook_token=rotate_webhook_token,
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


async def apply_graph_payload(
    *,
    profile_id: str,
    automation_id: int,
    spec_json: str,
) -> str:
    """Apply one graph spec to the selected automation and return JSON payload."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        item = await service.apply_graph(
            profile_id=profile_id,
            automation_id=automation_id,
            spec=AutomationGraphSpec.model_validate_json(spec_json),
        )
        return json.dumps({"graph": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    except ValueError as exc:
        return _error_json(error_code="invalid_graph_spec", reason=str(exc))
    finally:
        await engine.dispose()


async def graph_show_payload(*, profile_id: str, automation_id: int) -> str:
    """Return one active automation graph snapshot as deterministic JSON."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        item = await service.get_graph(profile_id=profile_id, automation_id=automation_id)
        return json.dumps({"graph": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def graph_validate_payload(*, profile_id: str, automation_id: int) -> str:
    """Return one graph validation report as deterministic JSON."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        item = await service.validate_graph(profile_id=profile_id, automation_id=automation_id)
        return json.dumps({"validation": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def graph_run_list_payload(
    *,
    profile_id: str,
    automation_id: int,
    limit: int = 20,
) -> str:
    """List recent graph runs for one automation."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        items = await service.list_graph_runs(
            profile_id=profile_id,
            automation_id=automation_id,
            limit=limit,
        )
        return json.dumps({"runs": [item.model_dump(mode="json") for item in items]}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def graph_run_show_payload(*, profile_id: str, run_id: int) -> str:
    """Return one graph run metadata payload."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        item = await service.get_graph_run(profile_id=profile_id, run_id=run_id)
        return json.dumps({"run": item.model_dump(mode="json")}, ensure_ascii=True)
    except AutomationsServiceError as exc:
        return _error_json(error_code=exc.error_code, reason=exc.reason)
    finally:
        await engine.dispose()


async def graph_trace_payload(*, profile_id: str, run_id: int) -> str:
    """Return one graph run trace payload."""

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    try:
        service = get_automations_service(settings)
        item = await service.get_graph_trace(profile_id=profile_id, run_id=run_id)
        return json.dumps({"trace": item.model_dump(mode="json")}, ensure_ascii=True)
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
