"""AgentLoop tests for internal subagent wait/result chaining."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.llm import LLMResponse, MockLLMProvider
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall, ToolContext, ToolResult
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def test_subagent_run_is_followed_by_internal_wait_and_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgentLoop should await subagent completion after successful subagent.run."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_subagent_wait_chain.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        calls: list[ToolCall] = []

        async def _fake_internal(
            *,
            run_id: int,
            session_id: str,
            ctx: ToolContext,
            tool_call: ToolCall,
        ) -> ToolResult:
            _ = run_id, session_id, ctx
            calls.append(tool_call)
            if tool_call.name == "subagent.wait":
                wait_calls = [item for item in calls if item.name == "subagent.wait"]
                if len(wait_calls) == 1:
                    return ToolResult(
                        ok=True,
                        payload={"task_id": "task-1", "status": "running", "done": False},
                    )
                return ToolResult(
                    ok=True,
                    payload={"task_id": "task-1", "status": "completed", "done": True},
                )
            if tool_call.name == "subagent.result":
                return ToolResult(
                    ok=True,
                    payload={"task_id": "task-1", "status": "completed", "output": "subagent-output"},
                )
            return ToolResult.error(error_code="unexpected_tool", reason=tool_call.name)

        monkeypatch.setattr(loop._tool_execution, "_execute_internal_tool_with_logging", _fake_internal)  # noqa: SLF001
        result = await loop._tool_execution.await_subagent_result_after_run(  # noqa: SLF001
            run_id=1,
            session_id="s-subagent",
            ctx=ToolContext(profile_id="default", session_id="s-subagent", run_id=1),
            run_result=ToolResult(
                ok=True,
                payload={
                    "task_id": "task-1",
                    "status": "running",
                    "subagent_name": "worker",
                    "timeout_sec": 30,
                },
            ),
        )

        assert result.ok is True
        assert result.payload["status"] == "completed"
        assert result.payload["output"] == "subagent-output"
        assert [call.name for call in calls] == ["subagent.wait", "subagent.wait", "subagent.result"]

    await engine.dispose()


async def test_subagent_wait_chain_checks_storage_cancel_between_internal_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage-backed cancellation should stop internal subagent wait polling."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_subagent_wait_cancel.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        calls: list[str] = []

        async def _fake_internal(
            *,
            run_id: int,
            session_id: str,
            ctx: ToolContext,
            tool_call: ToolCall,
        ) -> ToolResult:
            _ = run_id, session_id, ctx
            calls.append(tool_call.name)
            return ToolResult(
                ok=True,
                payload={"task_id": "task-1", "status": "running", "done": False},
            )

        async def _cancel_after_first_poll(*, run_id: int) -> None:
            _ = run_id
            if calls:
                raise asyncio.CancelledError

        monkeypatch.setattr(loop._tool_execution, "_execute_internal_tool_with_logging", _fake_internal)  # noqa: SLF001
        monkeypatch.setattr(loop._tool_execution, "_raise_if_cancel_requested", _cancel_after_first_poll)  # noqa: SLF001

        with pytest.raises(asyncio.CancelledError):
            await loop._tool_execution.await_subagent_result_after_run(  # noqa: SLF001
                run_id=1,
                session_id="s-subagent",
                ctx=ToolContext(profile_id="default", session_id="s-subagent", run_id=1),
                run_result=ToolResult(
                    ok=True,
                    payload={"task_id": "task-1", "status": "running", "timeout_sec": 30},
                ),
            )

        assert calls == ["subagent.wait"]

    await engine.dispose()
