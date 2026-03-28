"""Best-effort human-like reply pacing for Telegram transports."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channels.endpoint_contracts import ChannelReplyHumanizationConfig
from afkbot.services.channels.telegram_timeouts import TELEGRAM_ACTION_TIMEOUT_PREFIX
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_TYPING_REFRESH_SEC = 4.0


def compute_reply_humanization_delay_sec(
    *,
    text: str,
    config: ChannelReplyHumanizationConfig,
) -> float:
    """Estimate one bounded human-like typing delay for the outgoing message."""

    if not config.enabled:
        return 0.0
    normalized = text.strip()
    if not normalized:
        return 0.0
    estimated_ms = config.min_delay_ms + int((len(normalized) / config.chars_per_second) * 1000)
    bounded_ms = max(config.min_delay_ms, min(config.max_delay_ms, estimated_ms))
    return bounded_ms / 1000.0


async def simulate_telegram_bot_reply_humanization(
    *,
    settings: Settings,
    app_runtime: AppRuntime,
    profile_id: str,
    session_id: str,
    run_id: int,
    credential_profile_key: str | None,
    chat_id: str,
    thread_id: str | None,
    text: str,
    config: ChannelReplyHumanizationConfig,
) -> None:
    """Emit Bot API typing actions for one bounded delay before sending the reply."""

    delay_sec = compute_reply_humanization_delay_sec(text=text, config=config)
    if delay_sec <= 0:
        return
    params: dict[str, object] = {
        "chat_id": chat_id,
        "action": "typing",
    }
    if thread_id is not None:
        try:
            params["message_thread_id"] = int(thread_id)
        except ValueError:
            _LOGGER.warning("telegram_reply_humanization_invalid_thread_id thread_id=%s", thread_id)
    await _run_typing_loop(
        delay_sec=delay_sec,
        send_once=lambda: _send_telegram_typing_once(
            settings=settings,
            app_runtime=app_runtime,
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            credential_profile_key=credential_profile_key,
            params=params,
        ),
    )


async def simulate_telethon_reply_humanization(
    *,
    client: object,
    entity: object | None,
    text: str,
    config: ChannelReplyHumanizationConfig,
    mark_read_before_reply: bool,
    last_message_id: int | None,
) -> None:
    """Best-effort read receipt and typing delay before one Telethon reply."""

    if entity is not None and mark_read_before_reply and last_message_id is not None:
        await _send_telethon_read_ack(client=client, entity=entity, last_message_id=last_message_id)
    delay_sec = compute_reply_humanization_delay_sec(text=text, config=config)
    if delay_sec <= 0 or entity is None:
        return
    action_factory = getattr(client, "action", None)
    if callable(action_factory):
        try:
            action_context = action_factory(entity, "typing")
            enter = getattr(action_context, "__aenter__", None)
            exit_ = getattr(action_context, "__aexit__", None)
            if callable(enter) and callable(exit_):
                async with action_context:
                    await asyncio.sleep(delay_sec)
                return
        except Exception:
            _LOGGER.exception("telethon_reply_humanization_typing_failed")
    await asyncio.sleep(delay_sec)


async def _run_typing_loop(*, delay_sec: float, send_once: Any) -> None:
    remaining = max(delay_sec, 0.0)
    while remaining > 0:
        try:
            await send_once()
        except RuntimeError as exc:
            if _is_soft_telegram_action_timeout(exc):
                _LOGGER.warning(
                    "telegram_reply_humanization_action_timeout reason=%s",
                    exc,
                )
                return
            _LOGGER.exception("telegram_reply_humanization_action_failed")
            return
        except Exception:
            _LOGGER.exception("telegram_reply_humanization_action_failed")
            return
        sleep_for = min(_TYPING_REFRESH_SEC, remaining)
        await asyncio.sleep(sleep_for)
        remaining -= sleep_for


async def _send_telegram_typing_once(
    *,
    settings: Settings,
    app_runtime: AppRuntime,
    profile_id: str,
    session_id: str,
    run_id: int,
    credential_profile_key: str | None,
    params: dict[str, object],
) -> None:
    result = await app_runtime.run(
        app="telegram",
        action="send_chat_action",
        ctx=AppRuntimeContext(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            credential_profile_key=credential_profile_key,
            timeout_sec=min(settings.tool_timeout_default_sec, settings.tool_timeout_max_sec),
        ),
        params=params,
    )
    if not result.ok:
        raise RuntimeError(result.reason or "telegram send_chat_action failed")


def _is_soft_telegram_action_timeout(exc: RuntimeError) -> bool:
    """Return whether one Telegram chat-action failure is just a transport timeout."""

    message = str(exc)
    return message.startswith(TELEGRAM_ACTION_TIMEOUT_PREFIX)


async def _send_telethon_read_ack(
    *,
    client: object,
    entity: object,
    last_message_id: int,
) -> None:
    send_read_acknowledge = getattr(client, "send_read_acknowledge", None)
    if not callable(send_read_acknowledge):
        return
    try:
        await send_read_acknowledge(entity, max_id=last_message_id)
    except ValueError as exc:
        _LOGGER.warning("telethon_reply_humanization_read_ack_skipped reason=%s", exc)
    except Exception:
        _LOGGER.exception("telethon_reply_humanization_read_ack_failed")
