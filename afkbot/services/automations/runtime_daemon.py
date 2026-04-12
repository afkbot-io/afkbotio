"""Async runtime daemon for automation cron ticking and webhook ingress."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.services.automations.runtime_daemon_http import (
    RuntimeDaemonHttpRuntime,
    WebhookTokenValidator,
)
from afkbot.services.automations.runtime_daemon_validation import (
    dispose_validation_resources,
    prepare_validation_resources,
)
from afkbot.services.automations.runtime_http import (
    HttpReadError,
    HttpRequest,
    write_json_response,
)
from afkbot.services.automations.service import get_automations_service
from afkbot.settings import Settings, get_settings

_LOGGER = logging.getLogger(__name__)


class AutomationsRuntimeService(Protocol):
    """Subset of automation service methods required by runtime daemon."""

    async def trigger_webhook(
        self,
        *,
        profile_id: str,
        token: str,
        payload: Mapping[str, object],
    ) -> object:
        """Execute one webhook automation run."""
        ...

    async def tick_cron(
        self,
        *,
        now_utc: datetime,
        max_due_per_tick: int | None = None,
    ) -> object:
        """Execute one cron scheduler tick."""
        ...


@dataclass(frozen=True)
class _WebhookQueueTask:
    profile_id: str
    token: str
    payload: Mapping[str, object]


class RuntimeDaemon:
    """Long-running automation runtime with HTTP ingress and queue workers."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        host: str | None = None,
        port: int | None = None,
        service: AutomationsRuntimeService | None = None,
        webhook_token_validator: WebhookTokenValidator | None = None,
    ) -> None:
        """Initialize daemon state, queue, and HTTP runtime router."""

        resolved_settings = settings or get_settings()
        self._settings = resolved_settings
        self._host = resolved_settings.runtime_host if host is None else host
        self._port = resolved_settings.runtime_port if port is None else port
        self._service = service or get_automations_service(resolved_settings)
        self._webhook_token_validator = webhook_token_validator
        self._queue: asyncio.Queue[_WebhookQueueTask] = asyncio.Queue(
            maxsize=resolved_settings.runtime_queue_max_size
        )
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None
        self._cron_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._shutdown_event = asyncio.Event()
        self._shutting_down = False
        self._ready = False
        self._validation_engine: AsyncEngine | None = None
        self._validation_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._http_runtime = RuntimeDaemonHttpRuntime(
            settings=resolved_settings,
            enqueue_task=self._enqueue_task,
            is_ready=self._is_ready,
            is_shutting_down=lambda: self._shutting_down,
            webhook_token_validator=webhook_token_validator,
            validation_session_factory_getter=lambda: self._validation_session_factory,
            queue_task_factory=lambda profile_id, token, payload: _WebhookQueueTask(
                profile_id=profile_id,
                token=token,
                payload=payload,
            ),
        )

    @property
    def bound_port(self) -> int:
        """Return actual listening port; supports zero-port binding in tests."""

        if self._bound_port is None:
            raise RuntimeError("Runtime daemon is not started")
        return self._bound_port

    def begin_shutdown(self) -> None:
        """Switch daemon into shutdown mode and notify run loop."""

        if self._shutting_down:
            return
        self._shutting_down = True
        self._ready = False
        self._shutdown_event.set()

    async def start(self) -> None:
        """Start runtime HTTP server, cron loop, and worker pool."""

        if self._server is not None:
            return
        self._shutting_down = False
        self._shutdown_event.clear()
        self._ready = False

        server: asyncio.AbstractServer | None = None
        worker_tasks: list[asyncio.Task[None]] = []
        cron_task: asyncio.Task[None] | None = None
        try:
            await self._prepare_persistence()
            server = await asyncio.start_server(
                self._handle_client,
                host=self._host,
                port=self._port,
                limit=max(self._settings.runtime_max_header_bytes, 4096),
            )
            server_sockets = server.sockets
            if not server_sockets:
                raise RuntimeError("Runtime daemon failed to bind listening socket")
            worker_tasks = [
                asyncio.create_task(
                    self._worker_loop(worker_index=index),
                    name=f"automation-runtime-worker-{index}",
                )
                for index in range(self._settings.runtime_worker_count)
            ]
            cron_task = asyncio.create_task(self._cron_loop(), name="automation-runtime-cron")
        except Exception:
            await self._rollback_start_failure(
                server=server,
                worker_tasks=worker_tasks,
                cron_task=cron_task,
            )
            raise

        self._server = server
        self._bound_port = int(server.sockets[0].getsockname()[1])
        self._worker_tasks = worker_tasks
        self._cron_task = cron_task
        self._ready = True

    async def run_forever(self) -> None:
        """Run daemon until SIGINT/SIGTERM, then shut down gracefully."""

        await self.start()
        loop = asyncio.get_running_loop()
        registered_signals: list[signal.Signals] = []

        def _on_signal() -> None:
            self.begin_shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                continue
            registered_signals.append(sig)

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            self.begin_shutdown()
            raise
        finally:
            for sig in registered_signals:
                with suppress(NotImplementedError):
                    loop.remove_signal_handler(sig)
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stop accepting work and tear down runtime resources."""

        self.begin_shutdown()
        shutdown_timeout = max(self._settings.runtime_shutdown_timeout_sec, 0.0)

        if self._cron_task is not None:
            self._cron_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cron_task
            self._cron_task = None

        try:
            await asyncio.wait_for(self._queue.join(), timeout=shutdown_timeout)
        except asyncio.TimeoutError:
            pass

        if self._worker_tasks:
            for task in self._worker_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._worker_tasks, return_exceptions=True),
                    timeout=shutdown_timeout,
                )
            except asyncio.TimeoutError as exc:
                pending = [task for task in self._worker_tasks if not task.done()]
                if pending:
                    raise RuntimeError("Runtime worker shutdown timed out") from exc
            finally:
                self._worker_tasks = []

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._bound_port = None

        await self._dispose_validation_engine()

    async def _prepare_persistence(self) -> None:
        (
            self._validation_engine,
            self._validation_session_factory,
        ) = await prepare_validation_resources(
            settings=self._settings,
            webhook_token_validator=self._webhook_token_validator,
        )

    async def _cron_loop(self) -> None:
        interval = max(self._settings.runtime_cron_interval_sec, 0.001)
        loop = asyncio.get_running_loop()
        next_tick_at = loop.time()
        while not self._shutting_down:
            timeout = next_tick_at - loop.time()
            if timeout > 0:
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout)
                    break
                except asyncio.TimeoutError:
                    pass
            if self._shutting_down:
                break
            tick_started_at = loop.time()
            try:
                await self._service.tick_cron(
                    now_utc=datetime.now(timezone.utc),
                    max_due_per_tick=self._settings.runtime_cron_max_due_per_tick,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("automation_runtime_cron_tick_failed")
            next_tick_at = tick_started_at + interval

    async def _rollback_start_failure(
        self,
        *,
        server: asyncio.AbstractServer | None,
        worker_tasks: list[asyncio.Task[None]],
        cron_task: asyncio.Task[None] | None,
    ) -> None:
        """Tear down partial runtime state after start() failure."""

        if cron_task is not None:
            cron_task.cancel()
            with suppress(asyncio.CancelledError):
                await cron_task
        if worker_tasks:
            for task in worker_tasks:
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*worker_tasks, return_exceptions=True)
        if server is not None:
            server.close()
            await server.wait_closed()
        await self._dispose_validation_engine()

    async def _dispose_validation_engine(self) -> None:
        """Dispose token-validation engine resources if initialized."""

        await dispose_validation_resources(self._validation_engine)
        self._validation_engine = None
        self._validation_session_factory = None

    async def _worker_loop(self, *, worker_index: int) -> None:
        _ = worker_index
        while True:
            task = await self._queue.get()
            try:
                if isinstance(task, _WebhookQueueTask):
                    await self._service.trigger_webhook(
                        profile_id=task.profile_id,
                        token=task.token,
                        payload=task.payload,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("automation_runtime_webhook_task_failed")
            finally:
                self._queue.task_done()

    def _enqueue_task(self, task: _WebhookQueueTask) -> bool:
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            return False

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request = await self._read_request(reader)
        if isinstance(request, HttpReadError):
            await write_json_response(
                writer,
                status_code=request.status_code,
                payload=request.payload,
            )
            return
        status_code, payload = await self._route_request(request)
        await write_json_response(writer, status_code=status_code, payload=payload)

    async def _route_request(self, request: HttpRequest) -> tuple[int, Mapping[str, object]]:
        return await self._http_runtime.route_request(request)

    def _is_ready(self) -> bool:
        """Return readiness state including background task liveness."""

        if not self._ready or self._shutting_down or self._server is None:
            return False
        if self._cron_task is None or self._cron_task.done():
            return False
        if not self._worker_tasks:
            return False
        return all(not task.done() for task in self._worker_tasks)

    async def _read_request(self, reader: asyncio.StreamReader) -> HttpRequest | HttpReadError:
        return await self._http_runtime.read_request(reader)


async def run_runtime_daemon(*, host: str | None = None, port: int | None = None) -> None:
    """Start and run automation runtime daemon until interrupted."""

    daemon = RuntimeDaemon(host=host, port=port)
    await daemon.run_forever()
