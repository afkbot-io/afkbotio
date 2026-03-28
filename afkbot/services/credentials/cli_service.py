"""Service-layer helpers for credentials CLI commands."""

from __future__ import annotations

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.credentials import get_credentials_service
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.tools.plugins.credentials_list.plugin import serialize_binding_metadata
from afkbot.settings import Settings, get_settings


async def create_binding_payload(
    *,
    profile_id: str,
    app_name: str,
    profile_name: str,
    credential_slug: str,
    value: str,
    replace_existing: bool,
) -> dict[str, object]:
    """Create one credential binding and return CLI payload."""

    settings = get_settings()
    await _ensure_profile(settings=settings, profile_id=profile_id)
    metadata = await get_credentials_service(settings).create(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name=app_name,
        credential_profile_key=profile_name,
        credential_name=credential_slug,
        secret_value=value,
        replace_existing=replace_existing,
    )
    return {
        "ok": True,
        "binding": serialize_binding_metadata(metadata),
    }


async def update_binding_payload(
    *,
    profile_id: str,
    app_name: str,
    profile_name: str,
    credential_slug: str,
    value: str,
) -> dict[str, object]:
    """Update one credential binding and return CLI payload."""

    settings = get_settings()
    await _ensure_profile(settings=settings, profile_id=profile_id)
    metadata = await get_credentials_service(settings).update(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name=app_name,
        credential_profile_key=profile_name,
        credential_name=credential_slug,
        secret_value=value,
    )
    return {
        "ok": True,
        "binding": serialize_binding_metadata(metadata),
    }


async def delete_binding_payload(
    *,
    profile_id: str,
    app_name: str,
    profile_name: str,
    credential_slug: str,
) -> dict[str, object]:
    """Delete one credential binding and return CLI payload."""

    settings = get_settings()
    await _ensure_profile(settings=settings, profile_id=profile_id)
    await get_credentials_service(settings).delete(
        profile_id=profile_id,
        tool_name="app.run",
        integration_name=app_name,
        credential_profile_key=profile_name,
        credential_name=credential_slug,
    )
    return {
        "ok": True,
        "deleted": True,
        "app_name": app_name,
        "profile_name": profile_name,
        "credential_slug": credential_slug,
    }


async def list_bindings_payload(
    *,
    profile_id: str,
    app_name: str | None,
    profile_name: str | None,
    include_inactive: bool,
) -> dict[str, object]:
    """List credential bindings and return CLI payload."""

    settings = get_settings()
    await _ensure_profile(settings=settings, profile_id=profile_id)
    service = get_credentials_service(settings)
    if app_name is None:
        raw_rows = await service.list(
            profile_id=profile_id,
            tool_name=None,
            integration_name=None,
            credential_profile_key=profile_name,
            include_inactive=include_inactive,
        )
        rows = [item for item in raw_rows if item.tool_name == "app.run"]
    else:
        rows = await service.list_bindings_for_app_runtime(
            profile_id=profile_id,
            integration_name=app_name,
            tool_name="app.run",
            credential_profile_key=profile_name,
            include_inactive=include_inactive,
        )
    bindings = [serialize_binding_metadata(item) for item in rows]
    return {
        "ok": True,
        "bindings": bindings,
        "count": len(bindings),
    }


async def _ensure_profile(
    *,
    settings: Settings,
    profile_id: str,
) -> None:
    """Ensure runtime profile row exists before credentials CLI operations."""

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(session_factory) as db:
            await ProfileRepository(db).get_or_create_default(profile_id)
        get_profile_runtime_config_service(settings).ensure_layout(profile_id)
    finally:
        await engine.dispose()
