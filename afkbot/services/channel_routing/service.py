"""Persisted channel binding management and resolution service."""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.channel_binding_repo import ChannelBindingRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.channel_routing.contracts import (
    ChannelBindingRule,
    ChannelRoutingDiagnostics,
    ChannelRoutingDecision,
    ChannelRoutingInput,
    ChannelRoutingTelemetryEvent,
    ChannelRoutingTransportDiagnostics,
)
from afkbot.services.channel_routing.resolver import resolve_channel_binding
from afkbot.settings import Settings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_SERVICES_BY_ROOT: dict[tuple[str, int], "ChannelBindingService"] = {}
TRepoValue = TypeVar("TRepoValue")


class ChannelBindingServiceError(ValueError):
    """Structured channel binding service error."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class ChannelBindingService:
    """Manage persisted channel bindings and resolve runtime routing decisions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()
        self._telemetry_enabled = settings.channel_routing_telemetry_enabled
        self._telemetry_lock = asyncio.Lock()
        self._telemetry_counts: Counter[tuple[str, str]] = Counter()
        history_size = max(1, int(settings.channel_routing_telemetry_history_size))
        self._telemetry_events: deque[ChannelRoutingTelemetryEvent] = deque(maxlen=history_size)

    async def put(self, rule: ChannelBindingRule) -> ChannelBindingRule:
        """Create or replace one binding rule."""

        async def _op(session: AsyncSession) -> ChannelBindingRule:
            await self._ensure_profile_exists(session=session, profile_id=rule.profile_id)
            row = await ChannelBindingRepository(session).put(rule)
            return self._to_rule(row)

        return await self._with_session(_op)

    async def get(self, *, binding_id: str) -> ChannelBindingRule:
        """Return one binding rule by id."""

        async def _op(session: AsyncSession) -> ChannelBindingRule:
            row = await ChannelBindingRepository(session).get(binding_id)
            if row is None:
                raise ChannelBindingServiceError(
                    error_code="channel_binding_not_found",
                    reason=f"Channel binding not found: {binding_id}",
                )
            return self._to_rule(row)

        return await self._with_session(_op)

    async def list(
        self,
        *,
        transport: str | None = None,
        profile_id: str | None = None,
    ) -> list[ChannelBindingRule]:
        """List binding rules filtered by optional transport/profile."""

        async def _op(session: AsyncSession) -> list[ChannelBindingRule]:
            rows = await ChannelBindingRepository(session).list_all(
                transport=transport.strip() if transport else None,
                profile_id=profile_id.strip() if profile_id else None,
            )
            return [self._to_rule(row) for row in rows]

        return await self._with_session(_op)

    async def delete(self, *, binding_id: str) -> bool:
        """Delete one binding rule by id."""

        async def _op(session: AsyncSession) -> bool:
            deleted = await ChannelBindingRepository(session).delete(binding_id)
            if not deleted:
                raise ChannelBindingServiceError(
                    error_code="channel_binding_not_found",
                    reason=f"Channel binding not found: {binding_id}",
                )
            return True

        return await self._with_session(_op)

    async def resolve(self, *, routing_input: ChannelRoutingInput) -> ChannelRoutingDecision | None:
        """Resolve one inbound routing input against persisted binding rules."""

        async def _op(session: AsyncSession) -> ChannelRoutingDecision | None:
            rows = await ChannelBindingRepository(session).list_all(transport=routing_input.transport)
            rules = [self._to_rule(row) for row in rows]
            return resolve_channel_binding(bindings=rules, routing_input=routing_input)

        return await self._with_session(_op)

    async def record_outcome(self, *, event: ChannelRoutingTelemetryEvent) -> None:
        """Record one final runtime routing outcome for diagnostics."""

        if not self._telemetry_enabled:
            return
        transport = event.transport.strip().lower()
        async with self._telemetry_lock:
            self._telemetry_events.append(event)
            self._telemetry_counts[("__all__", "total")] += 1
            self._telemetry_counts[(transport, "total")] += 1
            if event.matched:
                self._telemetry_counts[("__all__", "matched")] += 1
                self._telemetry_counts[(transport, "matched")] += 1
            if event.fallback_used:
                self._telemetry_counts[("__all__", "fallback_used")] += 1
                self._telemetry_counts[(transport, "fallback_used")] += 1
            if event.no_match:
                self._telemetry_counts[("__all__", "no_match")] += 1
                self._telemetry_counts[(transport, "no_match")] += 1
            if event.strict and event.no_match:
                self._telemetry_counts[("__all__", "strict_no_match")] += 1
                self._telemetry_counts[(transport, "strict_no_match")] += 1

    async def diagnostics(self) -> ChannelRoutingDiagnostics:
        """Return aggregated in-memory routing telemetry snapshot."""

        if not self._telemetry_enabled:
            return ChannelRoutingDiagnostics(
                total=0,
                matched=0,
                fallback_used=0,
                no_match=0,
                strict_no_match=0,
                transports=(),
                recent_events=(),
            )
        async with self._telemetry_lock:
            transports = sorted(
                {
                    transport
                    for transport, _metric in self._telemetry_counts
                    if transport != "__all__"
                }
            )
            return ChannelRoutingDiagnostics(
                total=self._count("__all__", "total"),
                matched=self._count("__all__", "matched"),
                fallback_used=self._count("__all__", "fallback_used"),
                no_match=self._count("__all__", "no_match"),
                strict_no_match=self._count("__all__", "strict_no_match"),
                transports=tuple(
                    ChannelRoutingTransportDiagnostics(
                        transport=transport,
                        total=self._count(transport, "total"),
                        matched=self._count(transport, "matched"),
                        fallback_used=self._count(transport, "fallback_used"),
                        no_match=self._count(transport, "no_match"),
                        strict_no_match=self._count(transport, "strict_no_match"),
                    )
                    for transport in transports
                ),
                recent_events=tuple(self._telemetry_events),
            )

    @staticmethod
    def _to_rule(row: object) -> ChannelBindingRule:
        return ChannelBindingRule.model_validate(
            {
                "binding_id": getattr(row, "binding_id"),
                "transport": getattr(row, "transport"),
                "profile_id": getattr(row, "profile_id"),
                "session_policy": getattr(row, "session_policy"),
                "priority": getattr(row, "priority"),
                "enabled": getattr(row, "enabled"),
                "account_id": getattr(row, "account_id"),
                "peer_id": getattr(row, "peer_id"),
                "thread_id": getattr(row, "thread_id"),
                "user_id": getattr(row, "user_id"),
                "prompt_overlay": getattr(row, "prompt_overlay"),
            }
        )

    async def _ensure_profile_exists(self, *, session: AsyncSession, profile_id: str) -> None:
        row = await ProfileRepository(session).get(profile_id)
        if row is not None:
            return
        raise ChannelBindingServiceError(
            error_code="channel_binding_profile_not_found",
            reason=f"Profile not found: {profile_id}",
        )

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TRepoValue]],
    ) -> TRepoValue:
        await self._ensure_schema()
        async with session_scope(self._session_factory) as session:
            return await op(session)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True

    async def shutdown(self) -> None:
        """Dispose owned database engine."""

        await self._engine.dispose()

    def _count(self, transport: str, metric: str) -> int:
        return int(self._telemetry_counts[(transport, metric)])


def get_channel_binding_service(settings: Settings) -> ChannelBindingService:
    """Return loop-safe channel binding service instance for one runtime root."""

    root_key = str(settings.root_dir.resolve())
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        return ChannelBindingService(settings=settings)
    key = (root_key, loop_id)
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ChannelBindingService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def run_channel_binding_service_sync(
    settings: Settings,
    op: Callable[[ChannelBindingService], Awaitable[TRepoValue]],
) -> TRepoValue:
    """Run one channel-binding operation in a fresh event loop and dispose the engine."""

    async def _run() -> TRepoValue:
        service = ChannelBindingService(settings=settings)
        try:
            return await op(service)
        finally:
            await service.shutdown()

    return asyncio.run(_run())


def reset_channel_binding_services() -> None:
    """Reset cached channel binding services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_channel_binding_services_async() -> None:
    """Reset cached channel binding services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
