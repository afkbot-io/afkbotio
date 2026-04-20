"""Tests for credentials service error mapping and semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path

from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from sqlalchemy.exc import IntegrityError

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.credentials import (
    CredentialsServiceError,
    get_credentials_service,
    reset_credentials_services_async,
)
from afkbot.settings import get_settings


async def test_create_maps_integrity_error_to_credentials_conflict(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """IntegrityError during create should map to deterministic conflict code."""

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'svc_credentials.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()
    await reset_credentials_services_async()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    async def _raise_integrity(
        self: CredentialsRepository,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
        encrypted_value: str,
        key_version: str,
        replace_existing: bool = False,
    ) -> None:
        _ = (
            profile_id,
            integration_name,
            credential_profile_key,
            tool_name,
            credential_name,
            encrypted_value,
            key_version,
            replace_existing,
        )
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(CredentialsRepository, "create_binding", _raise_integrity)

    service = get_credentials_service(settings)
    try:
        await service.create(
            profile_id="default",
            tool_name="subagent.run",
            credential_name="api_token",
            secret_value="secret",
        )
    except CredentialsServiceError as exc:
        assert exc.error_code == "credentials_conflict"
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected CredentialsServiceError(credentials_conflict)")
    finally:
        await engine.dispose()


async def test_delete_profile_deactivates_bindings_for_runtime_resolution(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Deleting a credential profile should also make its bindings unreachable."""

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'svc_credentials_delete.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()
    await reset_credentials_services_async()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    service = get_credentials_service(settings)
    try:
        await service.create_profile(
            profile_id="default",
            integration_name="http",
            profile_key="ops",
        )
        await service.create(
            profile_id="default",
            tool_name="http.request",
            integration_name="http",
            credential_profile_key="ops",
            credential_name="api_key",
            secret_value="ops-secret",
            replace_existing=True,
        )

        deleted = await service.delete_profile(
            profile_id="default",
            integration_name="http",
            profile_key="ops",
        )
        assert deleted is True

        try:
            await service.resolve_plaintext_for_app_tool(
                profile_id="default",
                tool_name="http.request",
                integration_name="http",
                credential_profile_key="ops",
                credential_name="api_key",
            )
        except CredentialsServiceError as exc:
            assert exc.error_code == "credentials_missing"
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected deleted credential profile binding to become unreachable")
    finally:
        await engine.dispose()


async def test_deleted_explicit_profile_requires_reselection_when_other_profiles_exist(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime resolution should fail closed when an explicit profile key was deleted."""

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'svc_credentials_reselect.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()
    await reset_credentials_services_async()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    service = get_credentials_service(settings)
    try:
        await service.create_profile(
            profile_id="default",
            integration_name="http",
            profile_key="ops",
        )
        await service.create_profile(
            profile_id="default",
            integration_name="http",
            profile_key="default",
            is_default=True,
        )
        await service.create(
            profile_id="default",
            tool_name="http.request",
            integration_name="http",
            credential_profile_key="default",
            credential_name="api_key",
            secret_value="default-secret",
            replace_existing=True,
        )
        await service.delete_profile(
            profile_id="default",
            integration_name="http",
            profile_key="ops",
        )

        try:
            await service.resolve_effective_profile_key_for_app_tool(
                profile_id="default",
                tool_name="http.request",
                integration_name="http",
                credential_profile_key="ops",
                credential_name="api_key",
            )
        except CredentialsServiceError as exc:
            assert exc.error_code == "credential_profile_required"
            assert exc.details is not None
            assert exc.details["available_profile_keys"] == ["default"]
            assert exc.details["requested_profile_key"] == "ops"
        else:  # pragma: no cover - defensive
            raise AssertionError("Expected explicit deleted profile to fail closed")
    finally:
        await engine.dispose()


def test_get_credentials_service_returns_fresh_service_outside_running_loop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sync CLI call-sites should not reuse one async credentials service across loops."""

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'svc_credentials_registry.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", key)
    get_settings.cache_clear()
    asyncio.run(reset_credentials_services_async())

    settings = get_settings()
    first = get_credentials_service(settings)
    second = get_credentials_service(settings)

    assert first is not second
