"""Tests for health service integration matrix."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.browser_runtime import BrowserRuntimeStatus
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channels.endpoint_contracts import (
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channel_routing.runtime_target import resolve_runtime_target
from afkbot.services.credentials import (
    get_credentials_service,
    reset_credentials_services_async,
)
from afkbot.services.health import (
    HealthServiceError,
    run_channel_health_diagnostics,
    run_channel_routing_diagnostics,
    run_doctor,
    run_integration_matrix,
)
from afkbot.services.health.integration_probes import IntegrationProbeError
from afkbot.services.profile_runtime.contracts import ProfileRuntimeConfig
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.profile_runtime.runtime_secrets import get_profile_runtime_secrets_service
from afkbot.settings import Settings


async def _prepare_settings(tmp_path: Path, *, with_master_key: bool) -> Settings:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'health_matrix.db'}",
        root_dir=tmp_path,
        llm_api_key="test-llm-key",
        credentials_master_keys=(
            Fernet.generate_key().decode("utf-8")
            if with_master_key
            else None
        ),
    )
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
    await engine.dispose()
    return settings


async def test_integration_matrix_config_without_credentials_skips_app_integrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config matrix should mark app integrations as skipped when credentials are absent."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=False,
    )
    statuses = {item.integration: item.status for item in report.checks}

    assert statuses["http"] == "ok"
    assert statuses["llm"] == "ok"
    assert statuses["web.search"] == "ok"
    assert statuses["web.fetch"] == "ok"
    assert statuses["browser.control"] == "ok"
    assert statuses["app.list"] == "ok"
    assert statuses["credentials.request"] == "ok"
    assert statuses["telegram"] == "skip"
    assert statuses["imap"] == "skip"
    assert statuses["smtp"] == "skip"
    assert report.ok is True


async def test_integration_matrix_without_vault_keys_reports_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matrix should report deterministic vault error when master keys are unavailable."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=False)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=False,
    )
    by_name = {item.integration: item for item in report.checks}

    assert by_name["http"].status == "ok"
    assert by_name["llm"].status == "ok"
    assert by_name["web.search"].status == "ok"
    assert by_name["web.fetch"].status == "ok"
    assert by_name["browser.control"].status == "ok"
    assert by_name["app.list"].status == "ok"
    assert by_name["credentials.request"].status == "fail"
    assert by_name["credentials.request"].error_code == "credentials_vault_unavailable"
    assert by_name["telegram"].status == "fail"
    assert by_name["telegram"].error_code == "credentials_vault_unavailable"
    assert by_name["imap"].status == "fail"
    assert by_name["smtp"].status == "fail"
    assert report.ok is False


async def test_integration_matrix_probe_runs_real_llm_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe matrix should execute one live LLM probe while keeping config-only tools skipped."""

    # Arrange
    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    async def _fake_probe_integration(**kwargs: object) -> None:  # noqa: ANN003
        _ = kwargs
        return

    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.probe_integration",
        _fake_probe_integration,
    )

    # Act
    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=True,
    )
    by_name = {item.integration: item for item in report.checks}

    # Assert
    assert by_name["llm"].status == "ok"
    assert by_name["llm"].reason.startswith("probe passed (")
    for integration in (
        "web.search",
        "web.fetch",
        "app.list",
        "credentials.request",
    ):
        check = by_name[integration]
        assert check.status == "skip"
        assert check.error_code == "probe_not_supported"
    assert by_name["browser.control"].status == "ok"
    assert by_name["browser.control"].reason == "probe passed"


async def test_integration_matrix_probe_reports_llm_probe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe matrix should surface real LLM probe failures instead of reporting ready."""

    # Arrange
    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    async def _fake_probe_integration(**kwargs: object) -> None:  # noqa: ANN003
        spec = kwargs["spec"]
        if getattr(spec, "integration", None) == "llm":
            raise IntegrationProbeError(
                error_code="llm_provider_network_error",
                reason="LLM provider is temporarily unavailable. Please try again shortly.",
            )

    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.probe_integration",
        _fake_probe_integration,
    )

    # Act
    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=True,
    )
    by_name = {item.integration: item for item in report.checks}

    # Assert
    assert by_name["llm"].status == "fail"
    assert by_name["llm"].error_code == "llm_provider_network_error"
    assert by_name["llm"].reason == "LLM provider is temporarily unavailable. Please try again shortly."
    assert report.ok is False


async def test_integration_matrix_probe_reports_unexpected_llm_probe_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe matrix should convert unexpected LLM probe exceptions into one failed row."""

    # Arrange
    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    async def _fake_probe_integration(**kwargs: object) -> None:  # noqa: ANN003
        spec = kwargs["spec"]
        if getattr(spec, "integration", None) == "llm":
            raise RuntimeError("unexpected probe failure")

    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.probe_integration",
        _fake_probe_integration,
    )

    # Act
    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=True,
    )
    by_name = {item.integration: item for item in report.checks}

    # Assert
    assert by_name["llm"].status == "fail"
    assert by_name["llm"].error_code == "integration_probe_failed"
    assert by_name["llm"].reason == "RuntimeError: unexpected probe failure"
    assert report.ok is False


async def test_integration_matrix_browser_control_reports_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browser control should skip readiness when Playwright runtime is not installed."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_missing_package",
            reason="Playwright is not installed: ModuleNotFoundError",
            remediation="Run `afk browser install`.",
        ),
    )

    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=False,
    )
    by_name = {item.integration: item for item in report.checks}

    assert by_name["browser.control"].status == "skip"
    assert by_name["browser.control"].error_code == "browser_runtime_missing_package"
    assert "afk browser install" in by_name["browser.control"].reason


async def test_integration_matrix_browser_control_check_timeout_is_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected browser status probe failures should still fail doctor."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(
            ok=False,
            error_code="browser_runtime_check_timeout",
            reason="browser status probe exceeded 20 seconds",
            remediation="Run `afk browser install`.",
        ),
    )

    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=False,
    )
    by_name = {item.integration: item for item in report.checks}

    assert by_name["browser.control"].status == "fail"
    assert by_name["browser.control"].error_code == "browser_runtime_check_timeout"


async def test_run_doctor_creates_schema_before_ping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor should always create/update schema before DB ping."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_service.db'}",
        root_dir=tmp_path,
    )
    calls: list[str] = []

    async def _fake_create_schema(engine) -> None:  # type: ignore[no-untyped-def]
        _ = engine
        calls.append("create_schema")

    async def _fake_ping(engine) -> bool:  # type: ignore[no-untyped-def]
        _ = engine
        calls.append("ping")
        return True

    monkeypatch.setattr("afkbot.services.health.service.create_schema", _fake_create_schema)
    monkeypatch.setattr("afkbot.services.health.service.ping", _fake_ping)

    report = await run_doctor(settings)

    assert report.ok is True
    assert calls == ["create_schema", "ping"]


async def test_integration_matrix_uses_profile_effective_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matrix should resolve tool surface and secrets from the selected profile."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    runtime_configs = get_profile_runtime_config_service(settings)
    runtime_secrets = get_profile_runtime_secrets_service(settings)
    runtime_configs.write(
        "default",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            enabled_tool_plugins=("http_request",),
        ),
    )
    runtime_secrets.write("default", {"openai_api_key": "profile-openai-key"})
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    report = await run_integration_matrix(
        settings,
        profile_id="default",
        credential_profile_key="default",
        probe=False,
    )
    by_name = {item.integration: item for item in report.checks}

    assert by_name["llm"].status == "ok"
    assert by_name["llm"].reason == "ready (openai/gpt-4o-mini)"
    assert by_name["http"].status == "ok"
    assert by_name["web.search"].status == "fail"
    assert by_name["web.search"].error_code == "tool_not_registered"


async def test_channel_routing_diagnostics_report_recent_outcomes(tmp_path: Path) -> None:
    """Health diagnostics should expose aggregated routing telemetry."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing_health.db'}",
    )

    await resolve_runtime_target(
        settings=settings,
        explicit_profile_id="default",
        explicit_session_id="api-session",
        resolve_binding=True,
        transport="api",
        default_profile_id="default",
        default_session_id="api-session",
    )

    routing_report = await run_channel_routing_diagnostics(settings)

    assert routing_report.fallback_transports == ("api", "automation", "cli")
    assert routing_report.diagnostics.total == 1
    assert routing_report.diagnostics.fallback_used == 1
    assert routing_report.diagnostics.recent_events[-1].transport == "api"


async def test_channel_health_diagnostics_report_telegram_polling_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health diagnostics should expose Telegram polling adapter readiness."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    bindings = ChannelBindingService(settings)
    endpoint_service = get_channel_endpoint_service(settings)
    try:
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-main",
                transport="telegram",
                account_id="telegram-bot",
                profile_id="default",
                session_policy="per-thread",
            )
        )
        await endpoint_service.create(
            TelegramPollingEndpointConfig(
                endpoint_id="support-bot",
                profile_id="default",
                credential_profile_key="default",
                account_id="telegram-bot",
                enabled=False,
            )
        )

        class _FakeCredentialsService:
            pass

        async def _fake_available_credentials(**kwargs: object) -> set[str]:
            _ = kwargs
            return {"telegram_token"}

        monkeypatch.setattr(
            "afkbot.services.health.channel_diagnostics.get_credentials_service",
            lambda _settings: _FakeCredentialsService(),
        )
        monkeypatch.setattr(
            "afkbot.services.health.channel_diagnostics.available_credentials",
            _fake_available_credentials,
        )

        report = await run_channel_health_diagnostics(settings)

        assert len(report.telegram_polling) == 1
        telegram = report.telegram_polling[0]
        assert telegram.enabled is False
        assert telegram.profile_id == "default"
        assert telegram.profile_valid is True
        assert telegram.profile_exists is True
        assert telegram.token_configured is True
        assert telegram.binding_count == 1
        assert telegram.state_present is False
    finally:
        await bindings.shutdown()


async def test_channel_health_diagnostics_report_telethon_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Health diagnostics should expose Telethon user-channel readiness."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    bindings = ChannelBindingService(settings)
    endpoint_service = get_channel_endpoint_service(settings)
    credentials = get_credentials_service(settings)
    try:
        await bindings.put(
            ChannelBindingRule(
                binding_id="telethon-main",
                transport="telegram_user",
                account_id="tg-user",
                profile_id="default",
                session_policy="per-chat",
            )
        )
        await endpoint_service.create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user-main",
                account_id="tg-user",
                enabled=True,
                reply_mode="same_chat",
            )
        )
        await credentials.create(
            profile_id="default",
            tool_name="app.run",
            integration_name="telethon",
            credential_profile_key="tg-user-main",
            credential_name="api_id",
            secret_value="12345",
        )
        await credentials.create(
            profile_id="default",
            tool_name="app.run",
            integration_name="telethon",
            credential_profile_key="tg-user-main",
            credential_name="api_hash",
            secret_value="hash",
        )
        await credentials.create(
            profile_id="default",
            tool_name="app.run",
            integration_name="telethon",
            credential_profile_key="tg-user-main",
            credential_name="session_string",
            secret_value="session",
        )
        async def _fake_policy(**kwargs: object) -> bool:
            _ = kwargs
            return True

        monkeypatch.setattr(
            "afkbot.services.health.channel_diagnostics._telethon_policy_allows_runtime",
            _fake_policy,
        )

        report = await run_channel_health_diagnostics(settings)

        assert len(report.telegram_polling) == 0
        assert len(report.telethon_userbot) == 1
        telethon = report.telethon_userbot[0]
        assert telethon.endpoint_id == "personal-user"
        assert telethon.enabled is True
        assert telethon.api_id_configured is True
        assert telethon.api_hash_configured is True
        assert telethon.session_string_configured is True
        assert telethon.phone_configured is False
        assert telethon.binding_count == 1
        assert telethon.policy_allows_runtime is True
        assert telethon.state_present is False
    finally:
        await bindings.shutdown()


async def test_integration_matrix_fails_for_missing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor matrix should fail strictly when the selected profile does not exist."""

    await reset_credentials_services_async()
    settings = await _prepare_settings(tmp_path, with_master_key=True)
    monkeypatch.setattr(
        "afkbot.services.health.integration_matrix.get_browser_runtime_status",
        lambda: BrowserRuntimeStatus(ok=True, error_code=None, reason="ready"),
    )

    with pytest.raises(HealthServiceError) as exc_info:
        await run_integration_matrix(
            settings,
            profile_id="missing",
            credential_profile_key="default",
            probe=False,
        )

    assert exc_info.value.error_code == "profile_not_found"
