"""AgentLoop tests for async subagent.run execution semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.tool_execution_runtime import _PreparedToolExecution
from afkbot.services.llm import LLMResponse, MockLLMProvider
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall, ToolContext, ToolResult
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def test_subagent_run_returns_accepted_payload_without_internal_wait_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent turn should return accepted subagent task payload without waiting for completion."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_subagent_async_run.db")
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

        guarded_call = ToolCall(
            name="subagent.run",
            params={"prompt": "analyze", "subagent_name": "researcher"},
        )
        accepted_payload = {
            "task_id": "task-1",
            "status": "running",
            "subagent_name": "researcher",
            "timeout_sec": 30,
        }

        async def _fake_execute_tool_call(*, tool_call: ToolCall, ctx: ToolContext) -> ToolResult:
            assert tool_call == guarded_call
            assert ctx.profile_id == "default"
            assert ctx.session_id == "s-subagent"
            return ToolResult(ok=True, payload=accepted_payload)

        async def _unexpected_internal(**_: object) -> ToolResult:
            raise AssertionError("internal subagent.wait/result chain must not run")

        monkeypatch.setattr(loop._tool_execution, "execute_tool_call", _fake_execute_tool_call)  # noqa: SLF001
        monkeypatch.setattr(loop._tool_execution, "_execute_internal_tool_with_logging", _unexpected_internal)  # noqa: SLF001

        result = await loop._tool_execution._execute_prepared_tool_call(  # noqa: SLF001
            _PreparedToolExecution(
                run_id=1,
                session_id="s-subagent",
                ctx=ToolContext(profile_id="default", session_id="s-subagent", run_id=1),
                execution_name="subagent.run",
                sanitized_name="subagent.run",
                guarded_call=guarded_call,
                parallel_execution_safe=False,
            )
        )

        assert result.ok is True
        assert result.payload == accepted_payload

    await engine.dispose()
