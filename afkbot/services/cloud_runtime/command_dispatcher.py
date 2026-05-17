"""Control-plane command dispatcher for cloud-managed AFKBOT runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import json
import logging
from typing import Any

from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.cloud_runtime.gateway import CloudRuntimeCommand, CloudRuntimeGatewayClient
from afkbot.services.error_logging import redact_log_text
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.session_ids import compose_bounded_session_id, encode_session_component
from afkbot.settings import get_settings

logger = logging.getLogger(__name__)

RunChatTurn = Callable[..., Awaitable[TurnResult]]

_STOP_COMMANDS = frozenset({"shutdown", "runtime.stop"})
_ASYNC_COMMANDS = frozenset(
    {"automation.webhook", "chat.message", "task.create", "task.retry", "task.cancel"}
)
_DEFAULT_PROFILE_ID = "default"


class CloudRuntimeCommandDispatcher:
    """Dispatch cloud control-plane commands onto safe AFKBOT service entrypoints."""

    def __init__(
        self,
        *,
        gateway: CloudRuntimeGatewayClient,
        request_shutdown: Callable[[], None],
        run_chat_turn_fn: RunChatTurn = run_chat_turn,
    ) -> None:
        """Create a dispatcher.

        :param gateway: Connected cloud runtime gateway client.
        :param request_shutdown: Callback that starts local runtime shutdown.
        :param run_chat_turn_fn: Agent-loop entrypoint used for chat and task commands.
        :return: None.
        """

        self._gateway = gateway
        self._request_shutdown = request_shutdown
        self._run_chat_turn = run_chat_turn_fn
        self._tasks: set[asyncio.Task[None]] = set()
        self._task_commands_by_id: dict[str, asyncio.Task[None]] = {}

    async def handle(self, command: CloudRuntimeCommand) -> None:
        """Handle one control-plane command without blocking gateway reads.

        :param command: Parsed cloud runtime command.
        :return: None.
        """

        if command.command in _STOP_COMMANDS:
            await self._gateway.send_event(
                event_type="runtime.command.shutdown",
                message="Cloud control plane requested runtime shutdown.",
                payload={"command_id": command.command_id or "", "command": command.command},
            )
            self._request_shutdown()
            return

        if command.command not in _ASYNC_COMMANDS:
            await self._gateway.send_event(
                event_type="runtime.command.unsupported",
                severity="warning",
                message=f"Unsupported cloud command: {command.command}",
                payload={"command_id": command.command_id or "", "command": command.command},
            )
            return

        task = asyncio.create_task(
            self._handle_async_command(command),
            name=f"afk-cloud-command-{command.command}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        """Cancel in-flight cloud command work.

        :param: None.
        :return: None.
        """

        for task in tuple(self._tasks):
            task.cancel()
        for task in tuple(self._tasks):
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._task_commands_by_id.clear()

    async def _handle_async_command(self, command: CloudRuntimeCommand) -> None:
        try:
            if command.command == "chat.message":
                await self._handle_chat_message(command)
            elif command.command == "automation.webhook":
                await self._handle_automation_webhook(command)
            elif command.command in {"task.create", "task.retry"}:
                await self._handle_task_run(command)
            elif command.command == "task.cancel":
                await self._handle_task_cancel(command)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Cloud command failed: %s", redact_log_text(str(exc)))
            await self._gateway.send_event(
                event_type="runtime.command.failed",
                severity="error",
                message="Cloud command failed.",
                payload={
                    "command_id": command.command_id or "",
                    "command": command.command,
                    "error": redact_log_text(str(exc)),
                },
            )

    async def _handle_automation_webhook(self, command: CloudRuntimeCommand) -> None:
        payload = command.payload
        profile_id = _profile_id_from_payload(payload)
        token = _string_payload(payload, "token")
        raw_payload = payload.get("payload")
        webhook_payload = raw_payload if isinstance(raw_payload, dict) else {}
        if not token:
            await self._gateway.send_event(
                event_type="automation.webhook.rejected",
                severity="warning",
                message="Automation webhook was rejected.",
                payload={"command_id": command.command_id or "", "error_code": "webhook_token_required"},
            )
            return
        try:
            result = await get_automations_service(get_settings()).trigger_webhook(
                profile_id=profile_id,
                token=token,
                payload={str(key): value for key, value in webhook_payload.items()},
            )
        except AutomationsServiceError as exc:
            await self._gateway.send_event(
                event_type="automation.webhook.failed",
                severity="warning",
                message="Automation webhook failed.",
                payload={
                    "command_id": command.command_id or "",
                    "error_code": exc.error_code,
                    "reason": redact_log_text(exc.reason),
                },
            )
            return
        await self._gateway.send_event(
            event_type="automation.webhook.accepted",
            message="Automation webhook accepted.",
            payload={
                "automation_id": result.automation_id,
                "command_id": command.command_id or "",
                "deduplicated": result.deduplicated,
                "profile_id": result.profile_id,
                "session_id": result.session_id,
            },
        )

    async def _handle_chat_message(self, command: CloudRuntimeCommand) -> None:
        payload = command.payload
        content = _string_payload(payload, "content") or _string_payload(payload, "message")
        message_id = _string_payload(payload, "message_id") or command.command_id or ""
        if not content.strip():
            await self._gateway.send_chat_result(
                command_id=command.command_id,
                message_id=message_id,
                content="",
                payload={"error_code": "cloud_chat_message_empty"},
            )
            return

        result = await self._run_agent_turn(
            message=content,
            profile_id=_profile_id_from_payload(payload),
            session_id=_session_id("cloud-chat", message_id or content),
            client_msg_id=message_id or command.command_id,
            command=command,
        )
        await self._gateway.send_chat_result(
            command_id=command.command_id,
            message_id=message_id,
            content=result.envelope.message,
            payload={
                "action": result.envelope.action,
                "run_id": result.run_id,
                "profile_id": result.profile_id,
                "session_id": result.session_id,
            },
        )

    async def _handle_task_run(self, command: CloudRuntimeCommand) -> None:
        payload = command.payload
        task_key = _task_key(payload=payload, fallback=command.command_id or command.command)
        title = _string_payload(payload, "title") or "Cloud task"
        description = _string_payload(payload, "description")
        task_payload = _task_payload(
            payload=payload,
            command=command,
            status="running",
            title=title,
            description=description,
        )
        await self._gateway.send_task_update(
            command_id=command.command_id,
            message="Task started.",
            payload=task_payload,
        )

        current_task = asyncio.current_task()
        if current_task is not None:
            self._task_commands_by_id[task_key] = current_task
        try:
            result = await self._run_agent_turn(
                message=_task_prompt(title=title, description=description, payload=payload),
                profile_id=_profile_id_from_payload(payload),
                session_id=_session_id("cloud-task", task_key),
                client_msg_id=command.command_id or task_key,
                command=command,
            )
        except asyncio.CancelledError:
            await self._gateway.send_task_result(
                command_id=command.command_id,
                message="Task canceled.",
                payload={
                    **task_payload,
                    "status": "canceled",
                    "result": {"canceled": True},
                },
            )
            raise
        except Exception as exc:
            await self._gateway.send_task_result(
                command_id=command.command_id,
                message="Task failed.",
                payload={
                    **task_payload,
                    "status": "stuck",
                    "error_code": "cloud_task_failed",
                    "result": {"error": redact_log_text(str(exc))},
                },
            )
            raise
        finally:
            self._task_commands_by_id.pop(task_key, None)

        status = _task_status_for_turn(result)
        await self._gateway.send_task_result(
            command_id=command.command_id,
            message=result.envelope.message,
            payload={
                **task_payload,
                "status": status,
                "result": {
                    "action": result.envelope.action,
                    "message": result.envelope.message,
                    "run_id": result.run_id,
                    "profile_id": result.profile_id,
                    "session_id": result.session_id,
                },
            },
        )

    async def _handle_task_cancel(self, command: CloudRuntimeCommand) -> None:
        payload = command.payload
        task_key = _stable_task_key(payload)
        if not task_key:
            await self._gateway.send_task_result(
                command_id=command.command_id,
                message="Task cancel failed.",
                payload=_task_payload(
                    payload=payload,
                    command=command,
                    status="stuck",
                    title=_string_payload(payload, "title") or "Cloud task",
                    description=_string_payload(payload, "description"),
                    result={
                        "error_code": "cloud_task_cancel_target_required",
                        "message": "Task cancellation requires a stable task id.",
                    },
                ),
            )
            return
        running_task = self._task_commands_by_id.get(task_key)
        if running_task is None:
            await self._gateway.send_task_result(
                command_id=command.command_id,
                message="Task cancel failed.",
                payload=_task_payload(
                    payload=payload,
                    command=command,
                    status="stuck",
                    title=_string_payload(payload, "title") or "Cloud task",
                    description=_string_payload(payload, "description"),
                    result={
                        "error_code": "cloud_task_cancel_target_not_running",
                        "message": "No running task matched the requested task id.",
                    },
                ),
            )
            return
        running_task.cancel()
        await self._gateway.send_task_result(
            command_id=command.command_id,
            message="Task canceled.",
            payload=_task_payload(
                payload=payload,
                command=command,
                status="canceled",
                title=_string_payload(payload, "title") or "Cloud task",
                description=_string_payload(payload, "description"),
                result={"canceled": True},
            ),
        )

    async def _run_agent_turn(
        self,
        *,
        message: str,
        profile_id: str,
        session_id: str,
        client_msg_id: str | None,
        command: CloudRuntimeCommand,
    ) -> TurnResult:
        return await self._run_chat_turn(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            context_overrides=TurnContextOverrides(
                runtime_metadata={
                    "cloud_command": command.command,
                    "cloud_command_id": command.command_id or "",
                },
                trusted_runtime_context={
                    "ingress": "afkbot_cloud",
                    "command": command.command,
                },
            ),
            progress_sink=self._progress_sink(command=command),
        )

    def _progress_sink(self, *, command: CloudRuntimeCommand) -> Callable[[ProgressEvent], None]:
        """Build a non-blocking progress forwarder for Cloud chat/task events.

        :param command: Cloud command whose progress is being streamed.
        :return: Synchronous progress callback accepted by AgentLoop.
        """

        def _sink(event: ProgressEvent) -> None:
            payload = event.model_dump(mode="json")
            payload["command_id"] = command.command_id or ""
            payload["command"] = command.command
            tool_call_params = event.tool_call_params
            tool_progress = event.tool_progress
            tool_result = event.tool_result
            if tool_call_params is not None:
                payload["tool_call_params"] = tool_call_params
            if tool_progress is not None:
                payload["tool_progress"] = tool_progress
            if tool_result is not None:
                payload["tool_result"] = tool_result
            asyncio.create_task(
                self._gateway.send_event(
                    event_type="chat.progress" if command.command == "chat.message" else "task.progress",
                    message=_progress_message(event),
                    payload=payload,
                ),
                name=f"afk-cloud-progress-{event.event_id}",
            )

        return _sink


def _profile_id_from_payload(payload: dict[str, Any]) -> str:
    raw_profile_id = _string_payload(payload, "profile_id") or _DEFAULT_PROFILE_ID
    return validate_profile_id(raw_profile_id)


def _session_id(prefix: str, value: str) -> str:
    return compose_bounded_session_id(prefix, encode_session_component(value or "default"))


def _task_key(*, payload: dict[str, Any], fallback: str) -> str:
    return (
        _stable_task_key(payload)
        or fallback
    )


def _stable_task_key(payload: dict[str, Any]) -> str:
    return (
        _string_payload(payload, "task_id")
        or _string_payload(payload, "external_id")
        or _string_payload(payload, "control_plane_task_id")
    )


def _task_payload(
    *,
    payload: dict[str, Any],
    command: CloudRuntimeCommand,
    status: str,
    title: str,
    description: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = _string_payload(payload, "task_id") or _string_payload(payload, "external_id")
    control_plane_task_id = _string_payload(payload, "control_plane_task_id") or task_id
    task_payload: dict[str, Any] = {
        "task_id": task_id or command.command_id or "",
        "control_plane_task_id": control_plane_task_id or "",
        "status": status,
        "title": title[:240],
        "description": description,
        "command_id": command.command_id or "",
    }
    if result is not None:
        task_payload["result"] = result
    return task_payload


def _task_status_for_turn(result: TurnResult) -> str:
    if result.envelope.action == "finalize":
        return "done"
    if result.envelope.action in {"ask_question", "request_secure_field"}:
        return "waiting"
    if result.envelope.action == "block":
        return "stuck"
    return "done"


def _progress_message(event: ProgressEvent) -> str:
    if event.tool_name:
        return f"{event.stage}: {event.tool_name}"
    return event.stage


def _task_prompt(*, title: str, description: str, payload: dict[str, Any]) -> str:
    serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "\n\n".join(
        part
        for part in (
            f"Cloud task: {title}",
            description.strip(),
            f"Task payload: {serialized_payload}",
        )
        if part
    )


def _string_payload(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    return str(value).strip()
