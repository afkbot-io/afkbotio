"""Tests for channel runtime manager orchestration."""

from __future__ import annotations

from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    PartyFlowPollingEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory
from afkbot.services.channels.runtime_manager import (
    ChannelRuntimeManager,
    ChannelRuntimeManagerError,
)
from afkbot.services.channels.telethon_user import TelethonUserServiceError
from afkbot.services.channels.telegram_polling import TelegramPollingServiceError
from afkbot.settings import Settings


class _FakeEndpointService:
    def __init__(self, endpoints: tuple[ChannelEndpointConfig, ...]) -> None:
        self._endpoints = endpoints

    async def list(
        self,
        *,
        transport: str | None = None,
        enabled: bool | None = None,
        profile_id: str | None = None,
        endpoint_ids: tuple[str, ...] | None = None,
    ) -> list[ChannelEndpointConfig]:
        del transport, profile_id
        items = list(self._endpoints)
        if enabled is not None:
            items = [item for item in items if item.enabled == enabled]
        if endpoint_ids:
            allowed = set(endpoint_ids)
            items = [item for item in items if item.endpoint_id in allowed]
        return items

    def telegram_polling_state_path(self, *, endpoint_id: str):  # pragma: no cover - not used here
        raise AssertionError(f"Unexpected state path request: {endpoint_id}")

    def partyflow_polling_state_path(self, *, endpoint_id: str):  # pragma: no cover - not used here
        raise AssertionError(f"Unexpected state path request: {endpoint_id}")

    def telethon_user_state_path(self, *, endpoint_id: str):  # pragma: no cover - not used here
        raise AssertionError(f"Unexpected state path request: {endpoint_id}")

    def state_dir(self, *, endpoint_id: str):
        return f"/tmp/channel-state/{endpoint_id}"


class _FakeChannelService:
    def __init__(
        self,
        *,
        should_fail: bool = False,
        error_kind: str = "telegram",
        endpoint: ChannelEndpointConfig | None = None,
    ) -> None:
        self.should_fail = should_fail
        self.error_kind = error_kind
        self.started = False
        self.stopped = False
        self.endpoint = endpoint

    async def start(self) -> None:
        if self.should_fail:
            if self.error_kind == "telethon":
                raise TelethonUserServiceError(
                    error_code="telethon_session_unauthorized",
                    reason="Stored Telethon session is not authorized.",
                )
            raise TelegramPollingServiceError(
                error_code="telegram_polling_get_me_failed",
                reason="HTTPError: HTTP Error 401: Unauthorized",
            )
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeRuntimeManager(ChannelRuntimeManager):
    def __init__(
        self,
        settings: Settings,
        *,
        endpoint_service: _FakeEndpointService,
        service_by_endpoint_id: dict[str, _FakeChannelService],
    ) -> None:
        super().__init__(settings, endpoint_service=endpoint_service)
        self._service_by_endpoint_id = service_by_endpoint_id

    def _build_service(self, config: ChannelEndpointConfig) -> _FakeChannelService:
        return self._service_by_endpoint_id[config.endpoint_id]


def _endpoint(endpoint_id: str, *, enabled: bool = True) -> TelegramPollingEndpointConfig:
    return TelegramPollingEndpointConfig(
        endpoint_id=endpoint_id,
        profile_id="default",
        credential_profile_key="default",
        account_id=endpoint_id,
        enabled=enabled,
    )


def _telethon_endpoint(endpoint_id: str, *, enabled: bool = True) -> TelethonUserEndpointConfig:
    return TelethonUserEndpointConfig(
        endpoint_id=endpoint_id,
        profile_id="default",
        credential_profile_key="default",
        account_id=endpoint_id,
        enabled=enabled,
    )


def _partyflow_endpoint(
    endpoint_id: str, *, enabled: bool = True
) -> PartyFlowPollingEndpointConfig:
    return PartyFlowPollingEndpointConfig(
        endpoint_id=endpoint_id,
        profile_id="default",
        credential_profile_key="default",
        account_id=endpoint_id,
        enabled=enabled,
    )


def _legacy_partyflow_webhook_endpoint(
    endpoint_id: str, *, enabled: bool = True
) -> ChannelEndpointConfig:
    return ChannelEndpointConfig(
        endpoint_id=endpoint_id,
        transport="partyflow",
        adapter_kind="partyflow_webhook",
        profile_id="default",
        credential_profile_key="default",
        account_id=endpoint_id,
        enabled=enabled,
        config={"ingress_mode": "webhook"},
    )


def _plugin_endpoint(endpoint_id: str, *, enabled: bool = True) -> ChannelEndpointConfig:
    return ChannelEndpointConfig(
        endpoint_id=endpoint_id,
        transport="avito",
        adapter_kind="avito_polling",
        profile_id="default",
        credential_profile_key="avito-main",
        account_id="seller",
        enabled=enabled,
    )


async def test_runtime_manager_starts_all_enabled_endpoints(tmp_path) -> None:
    """Manager should start all enabled endpoints by default."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService(
        (_endpoint("a"), _telethon_endpoint("b"), _endpoint("c", enabled=False))
    )
    fake_services = {
        "a": _FakeChannelService(),
        "b": _FakeChannelService(),
        "c": _FakeChannelService(),
    }
    manager = _FakeRuntimeManager(
        settings,
        endpoint_service=endpoint_service,
        service_by_endpoint_id=fake_services,
    )

    started = await manager.start()

    assert started == ("a", "b")
    assert fake_services["a"].started is True
    assert fake_services["b"].started is True
    assert fake_services["c"].started is False


async def test_runtime_manager_wraps_channel_start_failure_and_stops_started_services(
    tmp_path,
) -> None:
    """Startup failures should surface as ChannelRuntimeManagerError and stop already-started adapters."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService((_endpoint("a"), _telethon_endpoint("b")))
    first = _FakeChannelService()
    second = _FakeChannelService(should_fail=True, error_kind="telethon")
    manager = _FakeRuntimeManager(
        settings,
        endpoint_service=endpoint_service,
        service_by_endpoint_id={"a": first, "b": second},
    )

    try:
        await manager.start()
    except ChannelRuntimeManagerError as exc:
        assert exc.error_code == "telethon_session_unauthorized"
        assert "Failed to start channel `b`" in exc.reason
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected ChannelRuntimeManagerError")

    assert first.started is True
    assert first.stopped is True
    assert second.started is False


async def test_runtime_manager_best_effort_keeps_other_channels_running(tmp_path) -> None:
    """Best-effort startup should collect failures and preserve successfully started adapters."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService(
        (_endpoint("a"), _telethon_endpoint("b"), _endpoint("c"))
    )
    first = _FakeChannelService()
    second = _FakeChannelService(should_fail=True, error_kind="telethon")
    third = _FakeChannelService()
    manager = _FakeRuntimeManager(
        settings,
        endpoint_service=endpoint_service,
        service_by_endpoint_id={"a": first, "b": second, "c": third},
    )

    report = await manager.start_best_effort()

    assert report.started_endpoint_ids == ("a", "c")
    assert len(report.failures) == 1
    assert report.failures[0].endpoint_id == "b"
    assert report.failures[0].error_code == "telethon_session_unauthorized"
    assert "Failed to start channel `b`" in report.failures[0].reason
    assert first.started is True
    assert first.stopped is False
    assert third.started is True
    assert third.stopped is False


async def test_runtime_manager_best_effort_converts_build_failures_to_report(tmp_path) -> None:
    """Best-effort startup should capture unsupported/build failures without aborting running services."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService((_endpoint("a"), _endpoint("broken"), _endpoint("c")))
    first = _FakeChannelService()
    third = _FakeChannelService()

    class _FailingBuildManager(_FakeRuntimeManager):
        def _build_service(self, config: ChannelEndpointConfig) -> _FakeChannelService:
            if config.endpoint_id == "broken":
                raise ChannelRuntimeManagerError(
                    error_code="channel_adapter_not_supported",
                    reason="Unsupported channel adapter transport=broken adapter_kind=missing",
                )
            return super()._build_service(config)

    manager = _FailingBuildManager(
        settings,
        endpoint_service=endpoint_service,
        service_by_endpoint_id={"a": first, "c": third},
    )

    report = await manager.start_best_effort()

    assert report.started_endpoint_ids == ("a", "c")
    assert len(report.failures) == 1
    assert report.failures[0].endpoint_id == "broken"
    assert report.failures[0].error_code == "channel_adapter_not_supported"
    assert first.started is True
    assert third.started is True


async def test_runtime_manager_starts_partyflow_polling_endpoints(tmp_path) -> None:
    """Manager should include PartyFlow polling endpoints in the supported startup set."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService((_partyflow_endpoint("partyflow-main"),))
    fake_service = _FakeChannelService()
    manager = _FakeRuntimeManager(
        settings,
        endpoint_service=endpoint_service,
        service_by_endpoint_id={"partyflow-main": fake_service},
    )

    started = await manager.start()

    assert started == ("partyflow-main",)
    assert fake_service.started is True


async def test_runtime_manager_strict_start_fails_for_legacy_partyflow_webhook(tmp_path) -> None:
    """Strict startup should not silently skip unsupported legacy PartyFlow webhook endpoints."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService(
        (_legacy_partyflow_webhook_endpoint("legacy-partyflow"),)
    )
    manager = ChannelRuntimeManager(settings, endpoint_service=endpoint_service)  # type: ignore[arg-type]

    try:
        await manager.start(endpoint_ids=("legacy-partyflow",))
    except ChannelRuntimeManagerError as exc:
        assert exc.error_code == "channel_adapter_not_supported"
        assert "adapter_kind=partyflow_webhook" in exc.reason
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected ChannelRuntimeManagerError")


async def test_runtime_manager_best_effort_reports_legacy_partyflow_webhook_failure(
    tmp_path,
) -> None:
    """Best-effort startup should report unsupported PartyFlow webhook rows as failures."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService(
        (_legacy_partyflow_webhook_endpoint("legacy-partyflow"),)
    )
    manager = ChannelRuntimeManager(settings, endpoint_service=endpoint_service)  # type: ignore[arg-type]

    report = await manager.start_best_effort()

    assert report.started_endpoint_ids == ()
    assert len(report.failures) == 1
    assert report.failures[0].endpoint_id == "legacy-partyflow"
    assert report.failures[0].error_code == "channel_adapter_not_supported"
    assert "adapter_kind=partyflow_webhook" in report.failures[0].reason


async def test_runtime_manager_starts_plugin_channel_adapter(tmp_path) -> None:
    """Manager should start plugin-provided channel adapters without core transport edits."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService((_plugin_endpoint("avito-main"),))
    fake_service = _FakeChannelService()
    adapter = ChannelAdapterFactory(
        transport="avito",
        adapter_kind="avito_polling",
        build_runtime=lambda settings, endpoint, state_dir: fake_service,
    )
    manager = ChannelRuntimeManager(
        settings,
        endpoint_service=endpoint_service,  # type: ignore[arg-type]
        channel_adapters={("avito", "avito_polling"): adapter},
    )

    started = await manager.start()

    assert started == ("avito-main",)
    assert fake_service.started is True


async def test_runtime_manager_validates_plugin_endpoint_before_runtime_start(tmp_path) -> None:
    """Plugin channel adapters should normalize endpoint config before runtime construction."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    endpoint_service = _FakeEndpointService((_plugin_endpoint("avito-main"),))
    built_services: list[_FakeChannelService] = []

    def _build_runtime(
        settings: Settings,
        endpoint: ChannelEndpointConfig,
        state_dir: str,
    ) -> _FakeChannelService:
        del settings, state_dir
        service = _FakeChannelService(endpoint=endpoint)
        built_services.append(service)
        return service

    adapter = ChannelAdapterFactory(
        transport="avito",
        adapter_kind="avito_polling",
        build_runtime=_build_runtime,
        validate_endpoint_config=lambda endpoint: endpoint.model_copy(
            update={"config": {"poll_interval_sec": 30}}
        ),
    )
    manager = ChannelRuntimeManager(
        settings,
        endpoint_service=endpoint_service,  # type: ignore[arg-type]
        channel_adapters={("avito", "avito_polling"): adapter},
    )

    started = await manager.start()

    assert started == ("avito-main",)
    assert built_services[0].endpoint is not None
    assert built_services[0].endpoint.config == {"poll_interval_sec": 30}
