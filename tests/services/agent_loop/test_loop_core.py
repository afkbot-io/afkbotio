"""Core AgentLoop integration tests for persistence and direct tool execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from afkbot.db.session import session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.models.profile import Profile
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.models.run import Run
from afkbot.models.runlog_event import RunlogEvent
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolBase, ToolCall, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def test_run_turn_persists_entities_and_progress_events(tmp_path: Path) -> None:
    """Run turn should persist running->completed artifacts with progress events."""

    settings, engine, factory = await create_test_db(tmp_path, "loop.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(session, ContextBuilder(settings, SkillLoader(settings)))
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-1",
            message="token abcdefghijklmnopqrstuvwxyz",
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "LLM provider is not configured. I could not execute this request."

        runs = (await session.execute(select(Run))).scalars().all()
        turns = (await session.execute(select(ChatTurn))).scalars().all()
        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )

        assert len(runs) == 1
        assert runs[0].status == "completed"
        assert len(turns) == 1
        assert len(events) == 5
        assert [event.event_type for event in events] == [
            "turn.think",
            "turn.progress",
            "turn.plan",
            "turn.progress",
            "turn.finalize",
        ]

        assert turns[0].user_message == "token abcdefghijklmnopqrstuvwxyz"
        assert turns[0].assistant_message == "LLM provider is not configured. I could not execute this request."

        payload = json.loads(events[-1].payload_json)
        assert payload["user_message"] == "token abcdefghijklmnopqrstuvwxyz"

    await engine.dispose()


async def test_run_turn_handles_concurrent_profile_bootstrap(tmp_path: Path) -> None:
    """Concurrent first turns should not fail profile/policy creation."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_concurrent.db")

    async def _one_turn(idx: int) -> None:
        async with session_scope(factory) as db:
            loop = AgentLoop(db, ContextBuilder(settings, SkillLoader(settings)))
            await loop.run_turn(profile_id="default", session_id=f"s-{idx}", message="hello")

    await asyncio.gather(_one_turn(1), _one_turn(2))

    async with session_scope(factory) as db:
        profiles = (await db.execute(select(Profile))).scalars().all()
        policies = (await db.execute(select(ProfilePolicy))).scalars().all()
        assert len(profiles) == 1
        assert len(policies) == 1

    await engine.dispose()


async def test_run_turn_executes_planned_tool_calls_and_logs_results(tmp_path: Path) -> None:
    """Bridge mode should execute planned calls and log sanitized call/results."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_tools.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-tools",
            message="final message",
            planned_tool_calls=[
                ToolCall(
                    name="debug.echo",
                    params={"message": "payload abcdefghijklmnopqrstuvwxyz"},
                ),
                ToolCall(name="missing.tool", params={"token": "abcdefghijklmnopqrstuvwxyz"}),
                ToolCall(name="debug.echo", params={"message": "ok", "timeout_sec": 1000}),
            ],
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message.startswith("One or more requested operations failed.")

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert [event.event_type for event in events] == [
            "turn.think",
            "turn.progress",
            "turn.plan",
            "turn.progress",
            "turn.progress",
            "tool.call",
            "tool.result",
            "tool.call",
            "tool.result",
            "tool.call",
            "tool.result",
            "turn.finalize",
        ]

        first_call = json.loads(events[5].payload_json)
        assert first_call["name"] == "debug.echo"
        assert first_call["params"]["message"] == "payload abcdefghijklmnopqrstuvwxyz"

        first_result = json.loads(events[6].payload_json)
        assert first_result["result"]["ok"] is True
        assert first_result["result"]["payload"]["message"] == "payload abcdefghijklmnopqrstuvwxyz"

        missing_result = json.loads(events[8].payload_json)
        assert missing_result["result"]["ok"] is False
        assert missing_result["result"]["error_code"] == "tool_not_found"

        invalid_result = json.loads(events[10].payload_json)
        assert invalid_result["result"]["ok"] is False
        assert invalid_result["result"]["error_code"] == "tool_params_invalid"

    await engine.dispose()


async def test_run_turn_returns_tool_not_found_for_mcp_names(tmp_path: Path) -> None:
    """Runtime tool execution must not resolve `mcp.*` names."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_mcp_boundary.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="default",
            session_id="s-mcp",
            message="run mcp tool",
            planned_tool_calls=[ToolCall(name="mcp.github.search", params={"query": "test"})],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )

        assert result_payload["name"] == "mcp.github.search"
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "tool_not_found"

    await engine.dispose()


async def test_automation_tool_requires_explicit_automation_intent(tmp_path: Path) -> None:
    """automation.* calls should be denied when user message has no automation intent."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_automation_intent_guard.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-automation-intent-guard",
            message="Привет",
            planned_tool_calls=[ToolCall(name="automation.list", params={})],
        )

        assert result.envelope.action == "finalize"
        assert "One or more requested operations failed." in result.envelope.message

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["name"] == "automation.list"
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "automation_intent_required"

    await engine.dispose()


async def test_automation_tool_allowed_with_automation_intent(tmp_path: Path) -> None:
    """automation.* calls should execute when automation intent is explicit."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_automation_intent_allow.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-automation-intent-allow",
            message="Покажи automation list",
            planned_tool_calls=[ToolCall(name="automation.list", params={})],
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message.startswith("Completed requested operations")

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["name"] == "automation.list"
        assert result_payload["result"]["ok"] is True

    await engine.dispose()


async def test_run_turn_propagates_cancellation_from_tool(tmp_path: Path) -> None:
    """CancelledError raised by tool should propagate from run turn."""

    class CancelTool(ToolBase):
        name = "debug.cancel"
        description = "cancellation test tool"
        parameters_model = ToolParameters

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            raise asyncio.CancelledError()

    settings, engine, factory = await create_test_db(tmp_path, "loop_tool_cancel.db")

    async with session_scope(factory) as session:
        registry = ToolRegistry([CancelTool()])
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=registry,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        with pytest.raises(asyncio.CancelledError):
            await loop.run_turn(
                profile_id="default",
                session_id="s-tool-cancel",
                message="hello",
                planned_tool_calls=[ToolCall(name="debug.cancel", params={})],
            )

    await engine.dispose()


def test_sanitize_value_keeps_non_secret_task_id() -> None:
    """Internal task identifiers should not be redacted as token-like secrets."""

    task_id = "abc123" * 6
    payload = AgentLoop._sanitize_value(  # noqa: SLF001
        {"task_id": task_id, "opaque": task_id},
    )
    assert isinstance(payload, dict)
    assert payload["task_id"] == task_id
    assert payload["opaque"] == "[REDACTED]"
