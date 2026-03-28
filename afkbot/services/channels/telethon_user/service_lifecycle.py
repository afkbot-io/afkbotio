"""Lifecycle helpers for the Telethon user-channel runtime service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from afkbot.services.channels.runtime_lease_registry import get_channel_runtime_lease_registry
from afkbot.services.channels.sender_registry import get_channel_sender_registry
from afkbot.services.channels.telethon_user.client import TelethonClientLike
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.normalization import TelethonUserIdentity

if TYPE_CHECKING:
    from afkbot.services.channels.telethon_user.contracts import TelethonResolvedCredentials
    from afkbot.services.channels.telethon_user.service import TelethonUserService


async def start_runtime(
    service: TelethonUserService,
    *,
    validate_profile_policy: Callable[..., Awaitable[None]],
    resolve_credentials: Callable[..., Awaitable[TelethonResolvedCredentials]],
    persist_identity_state: Callable[..., Awaitable[None]],
    import_telethon_module: Callable[[], Any],
) -> None:
    """Connect Telethon, register handlers, and start background tasks."""

    if service._runner_task is not None:
        return
    await validate_profile_policy(
        settings=service._settings,
        profile_id=service._endpoint.profile_id,
    )
    credentials = await resolve_credentials(
        settings=service._settings,
        profile_id=service._endpoint.profile_id,
        credential_profile_key=service._endpoint.credential_profile_key,
        require_session=True,
    )
    client = service._client_factory(
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        session_string=credentials.session_string,
    )
    sender_registered = False
    lease_acquired = False
    try:
        service._lease_owner_token = await get_channel_runtime_lease_registry(service._settings).acquire(
            transport=service._endpoint.transport,
            account_id=service._endpoint.account_id,
        )
        lease_acquired = True
        await client.connect()
        if not await client.is_user_authorized():
            raise TelethonUserServiceError(
                error_code="telethon_session_unauthorized",
                reason="Stored Telethon session is not authorized. Run `afk channel telethon authorize`.",
            )
        service._identity = service.resolve_identity(await client.get_me())
        await persist_identity_state(
            state_path=service._state_path,
            account_id=service._endpoint.account_id,
            identity=service._identity,
            last_error=None,
        )
        service._client = client
        if service._endpoint.watcher.enabled:
            await service._refresh_watched_dialogs(client=client)
        imported = import_telethon_module()
        builder_kwargs = {} if service._endpoint.process_self_commands else {"incoming": True}
        service._event_builder = imported.events_module.NewMessage(**builder_kwargs)
        client.add_event_handler(service._on_new_message, service._event_builder)
        if service._needs_live_sender_registration():
            await get_channel_sender_registry(service._settings).register(
                transport=service._endpoint.transport,
                account_id=service._endpoint.account_id,
                sender=service._send_text_via_live_client,
            )
            sender_registered = True
            service._sender_registered = True
        await service._restore_pending_ingress_events()
        service._stop_event.clear()
        service._worker_task = asyncio.create_task(
            service._worker_loop(),
            name=f"telethon-user-worker:{service._endpoint.endpoint_id}",
        )
        if service._endpoint.watcher.enabled:
            service._watcher_flush_task = asyncio.create_task(
                service._watcher_flush_loop(),
                name=f"telethon-user-watcher-flush:{service._endpoint.endpoint_id}",
            )
            service._watcher_refresh_task = asyncio.create_task(
                service._watcher_refresh_loop(),
                name=f"telethon-user-watcher-refresh:{service._endpoint.endpoint_id}",
            )
        service._runner_task = asyncio.create_task(
            service._run_until_disconnected(client),
            name=f"telethon-user-client:{service._endpoint.endpoint_id}",
        )
    except Exception:
        if sender_registered:
            await get_channel_sender_registry(service._settings).unregister(
                transport=service._endpoint.transport,
                account_id=service._endpoint.account_id,
                sender=service._send_text_via_live_client,
            )
            service._sender_registered = False
        if lease_acquired:
            await get_channel_runtime_lease_registry(service._settings).release(
                transport=service._endpoint.transport,
                account_id=service._endpoint.account_id,
                owner_token=service._lease_owner_token or "",
            )
            service._lease_owner_token = None
        for task in (
            service._runner_task,
            service._worker_task,
            service._watcher_flush_task,
            service._watcher_refresh_task,
            service._ingress_retry_task,
        ):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        service._runner_task = None
        service._worker_task = None
        service._watcher_flush_task = None
        service._watcher_refresh_task = None
        service._ingress_retry_task = None
        service._ingress_retry_deadline = None
        if service._event_builder is not None:
            try:
                client.remove_event_handler(service._on_new_message, service._event_builder)
            except Exception:
                pass
        service._event_builder = None
        service._client = None
        try:
            await client.disconnect()
        except Exception:
            pass
        raise


async def stop_runtime(service: TelethonUserService) -> None:
    """Stop worker tasks, unregister runtime state, and disconnect the client."""

    client = service._client
    if client is not None and service._event_builder is not None:
        try:
            client.remove_event_handler(service._on_new_message, service._event_builder)
        except Exception:
            pass
    if service._worker_task is not None:
        await service._queue.join()
    await service._ingress_coalescer.flush_all()
    service._stop_event.set()
    if service._sender_registered:
        await get_channel_sender_registry(service._settings).unregister(
            transport=service._endpoint.transport,
            account_id=service._endpoint.account_id,
            sender=service._send_text_via_live_client,
        )
        service._sender_registered = False
    if service._lease_owner_token is not None:
        await get_channel_runtime_lease_registry(service._settings).release(
            transport=service._endpoint.transport,
            account_id=service._endpoint.account_id,
            owner_token=service._lease_owner_token,
        )
        service._lease_owner_token = None
    service._client = None
    service._event_builder = None
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass
    for task in (
        service._runner_task,
        service._worker_task,
        service._watcher_flush_task,
        service._watcher_refresh_task,
        service._ingress_retry_task,
    ):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    service._runner_task = None
    service._worker_task = None
    service._watcher_flush_task = None
    service._watcher_refresh_task = None
    service._ingress_retry_task = None
    service._ingress_retry_deadline = None
    service._identity = None
    service._pending_restored = False
    async with service._watcher_lock:
        service._watched_dialogs.clear()
        service._watcher_buffer.clear()
        service._watcher_buffer_keys.clear()
        service._watcher_inflight_keys.clear()


async def probe_identity(
    service: TelethonUserService,
    *,
    resolve_credentials: Callable[..., Awaitable[TelethonResolvedCredentials]],
) -> TelethonUserIdentity:
    """Return the live Telethon identity without starting the full runtime."""

    if service._identity is not None:
        return service._identity
    credentials = await resolve_credentials(
        settings=service._settings,
        profile_id=service._endpoint.profile_id,
        credential_profile_key=service._endpoint.credential_profile_key,
        require_session=True,
    )
    client = service._client_factory(
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        session_string=credentials.session_string,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise TelethonUserServiceError(
                error_code="telethon_session_unauthorized",
                reason="Stored Telethon session is not authorized anymore.",
            )
        return service.resolve_identity(await client.get_me())
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def reset_state(service: TelethonUserService) -> bool:
    """Delete the persisted Telethon runtime state file when present."""

    if not service._state_path.exists():
        return False
    service._state_path.unlink()
    return True


async def run_until_disconnected(
    service: TelethonUserService,
    *,
    client: TelethonClientLike,
    persist_identity_state: Callable[..., Awaitable[None]],
) -> None:
    """Translate Telethon disconnect failures into structured service errors."""

    try:
        await client.run_until_disconnected()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await persist_identity_state(
            state_path=service._state_path,
            account_id=service._endpoint.account_id,
            identity=service._identity,
            last_error=f"{exc.__class__.__name__}: {exc}",
        )
        raise TelethonUserServiceError(
            error_code="telethon_runtime_failed",
            reason=f"{exc.__class__.__name__}: {exc}",
        ) from exc
