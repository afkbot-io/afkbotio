"""Runtime manager for starting/stopping enabled external channel adapters."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.channels.telethon_user import TelethonUserService, TelethonUserServiceError
from afkbot.services.channels.telegram_polling import TelegramPollingService, TelegramPollingServiceError
from afkbot.settings import Settings


class ChannelRuntimeManagerError(ValueError):
    """Structured channel runtime manager failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ChannelRuntimeStartFailure:
    """One channel adapter startup failure captured in best-effort mode."""

    endpoint_id: str
    error_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChannelRuntimeStartReport:
    """Startup result for best-effort channel boot."""

    started_endpoint_ids: tuple[str, ...]
    failures: tuple[ChannelRuntimeStartFailure, ...] = ()


class _ChannelRuntimeService(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ChannelRuntimeManager:
    """Load and run enabled channel adapters for one runtime root."""

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint_service: ChannelEndpointService | None = None,
    ) -> None:
        self._settings = settings
        self._endpoint_service = endpoint_service or get_channel_endpoint_service(settings)
        self._services: list[_ChannelRuntimeService] = []

    async def start(self, *, endpoint_ids: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Start all enabled endpoints, or one explicit subset when ids are provided."""

        endpoints = await self._load_endpoints(endpoint_ids=endpoint_ids)
        started: list[_ChannelRuntimeService] = []
        self._services = []
        try:
            for config in endpoints:
                service = self._build_service(config)
                try:
                    await service.start()
                except Exception as exc:
                    raise self._to_start_error(config=config, exc=exc) from exc
                self._services.append(service)
                started.append(service)
        except Exception:
            for service in reversed(started):
                with suppress(Exception):
                    await service.stop()
            self._services.clear()
            raise
        return tuple(config.endpoint_id for config in endpoints)

    async def start_best_effort(
        self,
        *,
        endpoint_ids: tuple[str, ...] = (),
    ) -> ChannelRuntimeStartReport:
        """Start endpoints and keep the runtime alive even when some channels fail."""

        endpoints = await self._load_endpoints(endpoint_ids=endpoint_ids)
        started_endpoint_ids: list[str] = []
        failures: list[ChannelRuntimeStartFailure] = []
        self._services = []
        for config in endpoints:
            try:
                service = self._build_service(config)
                await service.start()
            except Exception as exc:
                error = self._to_start_error(config=config, exc=exc)
                failures.append(
                    ChannelRuntimeStartFailure(
                        endpoint_id=config.endpoint_id,
                        error_code=error.error_code,
                        reason=error.reason,
                    )
                )
                continue
            self._services.append(service)
            started_endpoint_ids.append(config.endpoint_id)
        return ChannelRuntimeStartReport(
            started_endpoint_ids=tuple(started_endpoint_ids),
            failures=tuple(failures),
        )

    async def stop(self) -> None:
        """Stop all started channel adapters."""

        services = list(reversed(self._services))
        self._services.clear()
        for service in services:
            await service.stop()

    async def _load_endpoints(
        self,
        *,
        endpoint_ids: tuple[str, ...],
    ) -> tuple[ChannelEndpointConfig, ...]:
        try:
            if endpoint_ids:
                configs = await self._endpoint_service.list(endpoint_ids=endpoint_ids)
                found_ids = {item.endpoint_id for item in configs}
                missing = [item for item in endpoint_ids if item not in found_ids]
                if missing:
                    raise ChannelRuntimeManagerError(
                        error_code="channel_endpoint_not_found",
                        reason=f"Unknown channel endpoint(s): {', '.join(missing)}",
                    )
            else:
                configs = await self._endpoint_service.list(enabled=True)
        except ChannelEndpointServiceError as exc:
            raise ChannelRuntimeManagerError(
                error_code=exc.error_code,
                reason=exc.reason,
            ) from exc

        supported: list[ChannelEndpointConfig] = []
        for config in configs:
            if config.transport == "telegram" and config.adapter_kind == "telegram_bot_polling":
                supported.append(TelegramPollingEndpointConfig.model_validate(config.model_dump()))
            if config.transport == "telegram_user" and config.adapter_kind == "telethon_userbot":
                supported.append(TelethonUserEndpointConfig.model_validate(config.model_dump()))
        return tuple(supported)

    def _build_service(self, config: ChannelEndpointConfig) -> _ChannelRuntimeService:
        if config.transport == "telegram" and config.adapter_kind == "telegram_bot_polling":
            return TelegramPollingService(
                self._settings,
                endpoint=TelegramPollingEndpointConfig.model_validate(config.model_dump()),
                state_path=self._endpoint_service.telegram_polling_state_path(endpoint_id=config.endpoint_id),
            )
        if config.transport == "telegram_user" and config.adapter_kind == "telethon_userbot":
            return TelethonUserService(
                self._settings,
                endpoint=TelethonUserEndpointConfig.model_validate(config.model_dump()),
                state_path=self._endpoint_service.telethon_user_state_path(endpoint_id=config.endpoint_id),
            )
        raise ChannelRuntimeManagerError(
            error_code="channel_adapter_not_supported",
            reason=(
                f"Unsupported channel adapter transport={config.transport} "
                f"adapter_kind={config.adapter_kind}"
            ),
        )

    @staticmethod
    def _to_start_error(
        *,
        config: ChannelEndpointConfig,
        exc: Exception,
    ) -> ChannelRuntimeManagerError:
        if isinstance(exc, ChannelRuntimeManagerError):
            return exc
        if isinstance(exc, (TelegramPollingServiceError, TelethonUserServiceError)):
            return ChannelRuntimeManagerError(
                error_code=exc.error_code,
                reason=f"Failed to start channel `{config.endpoint_id}`: {exc.reason}",
            )
        return ChannelRuntimeManagerError(
            error_code="channel_start_failed",
            reason=(
                f"Failed to start channel `{config.endpoint_id}`: "
                f"{exc.__class__.__name__}: {exc}"
            ),
        )
