"""Tests for cloud runtime command dispatching."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.automations.contracts import AutomationWebhookTriggerResult
from afkbot.services.cloud_runtime.command_dispatcher import CloudRuntimeCommandDispatcher
from afkbot.services.cloud_runtime.gateway import CloudRuntimeCommand


class _FakeGateway:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.chat_results: list[dict[str, Any]] = []
        self.task_updates: list[dict[str, Any]] = []
        self.task_results: list[dict[str, Any]] = []

    async def send_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def send_chat_result(self, **kwargs: Any) -> None:
        self.chat_results.append(kwargs)

    async def send_task_update(self, **kwargs: Any) -> None:
        self.task_updates.append(kwargs)

    async def send_task_result(self, **kwargs: Any) -> None:
        self.task_results.append(kwargs)


@pytest.mark.asyncio
async def test_dispatcher_runs_chat_message() -> None:
    """chat.message should run one canonical agent turn and send chat.result."""

    gateway = _FakeGateway()
    calls: list[dict[str, Any]] = []

    async def _run_chat_turn(**kwargs: Any) -> TurnResult:
        calls.append(kwargs)
        return TurnResult(
            run_id=42,
            session_id=kwargs["session_id"],
            profile_id=kwargs["profile_id"],
            envelope=ActionEnvelope(action="finalize", message="hello from cloud"),
        )

    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
        run_chat_turn_fn=_run_chat_turn,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="chat.message",
            command_id="cmd-1",
            payload={"message_id": "msg-1", "content": "hello", "profile_id": "default"},
        )
    )
    await _drain_dispatcher(dispatcher)

    assert calls[0]["message"] == "hello"
    assert calls[0]["profile_id"] == "default"
    assert gateway.chat_results[0]["content"] == "hello from cloud"
    assert gateway.chat_results[0]["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_dispatcher_forwards_chat_progress_events() -> None:
    """chat.message should forward AgentLoop progress to Cloud events."""

    gateway = _FakeGateway()

    async def _run_chat_turn(**kwargs: Any) -> TurnResult:
        progress_sink = kwargs["progress_sink"]
        progress_sink(
            ProgressEvent(
                event_id=1,
                run_id=9,
                stage="tool_call",
                tool_name="browser.open",
                event_type="tool.call",
                payload={"tool": "browser.open"},
            )
        )
        await asyncio.sleep(0)
        return TurnResult(
            run_id=9,
            session_id=kwargs["session_id"],
            profile_id=kwargs["profile_id"],
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
        run_chat_turn_fn=_run_chat_turn,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="chat.message",
            command_id="cmd-progress",
            payload={"message_id": "msg-progress", "content": "check page"},
        )
    )
    await _drain_dispatcher(dispatcher)

    assert gateway.events[0]["event_type"] == "chat.progress"
    assert gateway.events[0]["message"] == "tool_call: browser.open"
    assert gateway.events[0]["payload"]["command_id"] == "cmd-progress"


@pytest.mark.asyncio
async def test_dispatcher_reports_task_result() -> None:
    """task.create should emit running and terminal states for the control plane."""

    gateway = _FakeGateway()

    async def _run_chat_turn(**kwargs: Any) -> TurnResult:
        return TurnResult(
            run_id=7,
            session_id=kwargs["session_id"],
            profile_id=kwargs["profile_id"],
            envelope=ActionEnvelope(action="finalize", message="task done"),
        )

    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
        run_chat_turn_fn=_run_chat_turn,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="task.create",
            command_id="cmd-task",
            payload={
                "task_id": "task-1",
                "title": "Write summary",
                "description": "Summarize the current workspace.",
            },
        )
    )
    await _drain_dispatcher(dispatcher)

    assert gateway.task_updates[0]["payload"]["status"] == "running"
    assert gateway.task_results[0]["payload"]["status"] == "done"
    assert gateway.task_results[0]["payload"]["result"]["message"] == "task done"


@pytest.mark.asyncio
async def test_dispatcher_rejects_cancel_without_stable_task_id() -> None:
    """task.cancel should not acknowledge success when no target task can be identified."""

    gateway = _FakeGateway()
    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(command="task.cancel", command_id="cancel-1", payload={})
    )
    await _drain_dispatcher(dispatcher)

    assert gateway.task_results[0]["message"] == "Task cancel failed."
    assert gateway.task_results[0]["payload"]["status"] == "stuck"
    assert (
        gateway.task_results[0]["payload"]["result"]["error_code"]
        == "cloud_task_cancel_target_required"
    )


@pytest.mark.asyncio
async def test_dispatcher_rejects_cancel_for_unknown_running_task() -> None:
    """task.cancel should fail explicitly when the target task is not running."""

    gateway = _FakeGateway()
    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="task.cancel",
            command_id="cancel-2",
            payload={"control_plane_task_id": "task-404"},
        )
    )
    await _drain_dispatcher(dispatcher)

    assert gateway.task_results[0]["message"] == "Task cancel failed."
    assert gateway.task_results[0]["payload"]["status"] == "stuck"
    assert (
        gateway.task_results[0]["payload"]["result"]["error_code"]
        == "cloud_task_cancel_target_not_running"
    )


@pytest.mark.asyncio
async def test_dispatcher_stops_runtime_for_shutdown_command() -> None:
    """shutdown commands should go through the shared runtime shutdown callback."""

    gateway = _FakeGateway()
    stopped = False

    def _request_shutdown() -> None:
        nonlocal stopped
        stopped = True

    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=_request_shutdown,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(command="runtime.stop", command_id="cmd-stop", payload={})
    )

    assert stopped is True
    assert gateway.events[0]["event_type"] == "runtime.command.shutdown"


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_profile_id() -> None:
    """Invalid Cloud profile ids should fail explicitly instead of falling back."""

    gateway = _FakeGateway()
    calls: list[dict[str, Any]] = []

    async def _run_chat_turn(**kwargs: Any) -> TurnResult:
        calls.append(kwargs)
        return TurnResult(
            run_id=1,
            session_id=kwargs["session_id"],
            profile_id=kwargs["profile_id"],
            envelope=ActionEnvelope(action="finalize", message="should not run"),
        )

    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
        run_chat_turn_fn=_run_chat_turn,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="chat.message",
            command_id="cmd-bad-profile",
            payload={"message_id": "msg-1", "content": "hello", "profile_id": "../bad"},
        )
    )
    await _drain_dispatcher(dispatcher)

    assert calls == []
    assert gateway.events[0]["event_type"] == "runtime.command.failed"
    assert gateway.events[0]["payload"]["command_id"] == "cmd-bad-profile"


@pytest.mark.asyncio
async def test_dispatcher_triggers_webhook_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    """automation.webhook should call the local automation service by token."""

    gateway = _FakeGateway()
    calls: list[dict[str, Any]] = []

    class _FakeAutomationService:
        async def trigger_webhook(self, **kwargs: Any) -> AutomationWebhookTriggerResult:
            calls.append(kwargs)
            return AutomationWebhookTriggerResult(
                automation_id=10,
                profile_id=kwargs["profile_id"],
                session_id="automation-webhook-10",
                payload={"ok": True},
                deduplicated=False,
            )

    monkeypatch.setattr(
        "afkbot.services.cloud_runtime.command_dispatcher.get_automations_service",
        lambda settings: _FakeAutomationService(),
    )
    dispatcher = CloudRuntimeCommandDispatcher(
        gateway=gateway,  # type: ignore[arg-type]
        request_shutdown=lambda: None,
    )

    await dispatcher.handle(
        CloudRuntimeCommand(
            command="automation.webhook",
            command_id="cmd-hook",
            payload={
                "profile_id": "default",
                "token": "hook-token",
                "payload": {"level": "error"},
            },
        )
    )
    await _drain_dispatcher(dispatcher)

    assert calls == [
        {
            "profile_id": "default",
            "token": "hook-token",
            "payload": {"level": "error"},
        }
    ]
    assert gateway.events[0]["event_type"] == "automation.webhook.accepted"
    assert gateway.events[0]["payload"]["automation_id"] == 10


async def _drain_dispatcher(dispatcher: CloudRuntimeCommandDispatcher) -> None:
    """Let scheduled command tasks finish.

    :param dispatcher: Dispatcher under test.
    :return: None.
    """

    await asyncio.sleep(0)
    await dispatcher.close()
