"""AgentLoop tests for LLM iteration, history, and planner-visible policy behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from afkbot.models.chat_session_compaction import ChatSessionCompaction
from afkbot.db.session import session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.models.runlog_event import RunlogEvent
from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.llm_tool_followup import LLMToolFollowupPolicy
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.llm import BaseLLMProvider, LLMRequest, LLMResponse, MockLLMProvider, ToolCallRequest
from afkbot.services.tools.base import ToolBase, ToolContext, ToolParameters
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall, ToolResult
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings
from tests.services.agent_loop._loop_harness import SleepLLMProvider, create_test_db


def _tool_followup_policy(settings: Settings) -> LLMToolFollowupPolicy:
    """Build the extracted follow-up policy used by the LLM iteration runtime."""

    return LLMToolFollowupPolicy(
        tool_skill_resolver=ToolSkillResolver(
            settings=settings,
            tool_registry=ToolRegistry.from_settings(settings),
        )
    )


async def test_llm_hides_automation_tools_without_automation_intent(tmp_path: Path) -> None:
    """LLM tool catalog should exclude automation tools for non-automation prompts."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_hide_automation_tools.db")
    provider = MockLLMProvider([LLMResponse.final("ok")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="default",
            session_id="s-llm-hide-automation-tools",
            message="send telegram message",
        )

        assert provider.requests
        names = {tool.name for tool in provider.requests[0].available_tools}
        assert "app.run" in names
        assert "automation.create" not in names
        assert "automation.list" not in names

    await engine.dispose()


async def test_llm_keeps_automation_tools_with_automation_intent(tmp_path: Path) -> None:
    """LLM tool catalog should keep automation tools when automation intent is explicit."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_keep_automation_tools.db")
    provider = MockLLMProvider([LLMResponse.final("ok")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="default",
            session_id="s-llm-keep-automation-tools",
            message="create automation with cron schedule",
        )

        assert provider.requests
        names = {tool.name for tool in provider.requests[0].available_tools}
        assert "automation.create" in names
        assert "automation.list" in names

    await engine.dispose()


async def test_iterative_llm_tool_loop_until_finalize(tmp_path: Path) -> None:
    """LLM mode should iterate tool calls then finalize on final response."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm.db")

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "from llm token-abcdefghijklmnop"},
                    )
                ]
            ),
            LLMResponse.final("done final token-abcdefghijklmnop"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(profile_id="default", session_id="s-llm", message="hello")

        assert result.envelope.message == "done final token-abcdefghijklmnop"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in events]
        assert "tool.call" in event_types
        assert "tool.result" in event_types
        assert event_types[0:4] == ["turn.think", "turn.progress", "turn.plan", "turn.progress"]
        assert event_types[-1] == "turn.finalize"

    await engine.dispose()


async def test_llm_can_finalize_directly_from_tool_display_text(tmp_path: Path) -> None:
    """One successful tool payload with display_text should skip a second LLM pass."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_display_text.db")

    class _DisplayTool(ToolBase):
        name = "debug.display"
        description = "Return one deterministic display text."
        parameters_model = ToolParameters

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            return ToolResult(ok=True, payload={"display_text": "Marketplace skills in `default`:\n- `figma`"})

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="debug.display",
                        params={},
                    )
                ]
            ),
            LLMResponse.final("this should never be used"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_DisplayTool()]),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(profile_id="default", session_id="s-llm-display-text", message="hello")

        assert result.envelope.message == "Marketplace skills in `default`:\n- `figma`"

    assert len(scripted.requests) == 1
    await engine.dispose()


async def test_llm_history_keeps_assistant_tool_call_linkage(tmp_path: Path) -> None:
    """Second LLM iteration must include assistant tool_calls and tool_call_id linkage."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_history.db")
    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "from llm"},
                        call_id="call_debug_1",
                    )
                ]
            ),
            LLMResponse.final("done"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-linkage",
            message="hello",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) >= 2
    second_history = scripted.requests[1].history
    assistant_tool_message = next(
        (msg for msg in second_history if msg.role == "assistant" and msg.tool_calls),
        None,
    )
    assert assistant_tool_message is not None
    assert assistant_tool_message.tool_calls[0].call_id == "call_debug_1"
    assert assistant_tool_message.tool_calls[0].name == "debug.echo"

    tool_result_message = next((msg for msg in second_history if msg.role == "tool"), None)
    assert tool_result_message is not None
    assert tool_result_message.tool_call_id == "call_debug_1"
    assert tool_result_message.tool_name == "debug.echo"

    await engine.dispose()


async def test_llm_overflow_retries_with_automatic_context_compaction(tmp_path: Path) -> None:
    """Overflow rejections should trigger hybrid compaction and retry within the same iteration."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_overflow_compaction.db")

    class _OverflowRecoveryProvider(BaseLLMProvider):
        def __init__(self) -> None:
            self.requests: list[LLMRequest] = []
            self._main_calls = 0

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            if request.session_id == "system-compaction":
                return LLMResponse.final(
                    "Goal: continue the conversation\nCompleted: earlier discussion summarized\nNext: answer the latest user message"
                )
            self._main_calls += 1
            if self._main_calls < 3:
                return LLMResponse.final(f"seed-{self._main_calls}")
            if self._main_calls == 3:
                return LLMResponse.final(
                    "overflow",
                    error_code="llm_context_window_exceeded",
                    error_detail="Your input exceeds the context window of this model.",
                )
            return LLMResponse.final("recovered final")

    provider = _OverflowRecoveryProvider()

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            llm_max_iterations=1,
            session_compaction_keep_recent_turns=1,
            session_compaction_max_chars=320,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(profile_id="default", session_id="s-overflow", message="first")
        await loop.run_turn(profile_id="default", session_id="s-overflow", message="second")
        result = await loop.run_turn(profile_id="default", session_id="s-overflow", message="third")

        assert result.envelope.message == "recovered final"
        assert len(provider.requests) == 5
        retry_request = provider.requests[-1]
        assert retry_request.history[0].role == "system"
        assert "continue the conversation" in (retry_request.history[0].content or "")

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in events]
        assert "llm.call.compaction_start" in event_types
        assert "llm.call.compaction_done" in event_types
        compaction_done = next(event for event in events if event.event_type == "llm.call.compaction_done")
        payload = json.loads(compaction_done.payload_json)
        assert payload["summary_strategy"] == "hybrid_llm_v1"
        assert payload["history_messages_before"] > payload["history_messages_after"]

    await engine.dispose()


async def test_llm_history_keeps_provider_item_ids_for_responses_replay(tmp_path: Path) -> None:
    """Responses provider item ids should survive sanitizer replay into the next iteration."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_provider_items.db")
    reasoning_id = "abc123def456ghi789jkl012"
    function_call_id = "zyx987wvu654tsr321qpo000"
    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "from llm"},
                        call_id="call_debug_1",
                    )
                ],
                provider_items=[
                    {"type": "reasoning", "id": reasoning_id},
                    {
                        "type": "function_call",
                        "id": function_call_id,
                        "call_id": "call_debug_1",
                        "name": "debug_echo",
                        "arguments": '{"message":"from llm"}',
                    },
                ],
            ),
            LLMResponse.final("done"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-provider-items",
            message="hello",
        )

        # Assert
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) >= 2
    second_history = scripted.requests[1].history
    assistant_provider_message = next(
        (msg for msg in second_history if msg.role == "assistant" and msg.provider_items),
        None,
    )
    assert assistant_provider_message is not None
    assert assistant_provider_message.provider_items[0]["id"] == reasoning_id
    assert assistant_provider_message.provider_items[1]["id"] == function_call_id
    assert assistant_provider_message.provider_items[1]["call_id"] == "call_debug_1"

    await engine.dispose()


async def test_llm_request_timeout_returns_finalize_with_timeout_error(tmp_path: Path) -> None:
    """Turn should finish with deterministic timeout message when LLM call exceeds timeout."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_timeout.db")
    timeout_provider = SleepLLMProvider(
        sleep_sec=0.2,
        response=LLMResponse.final("late response"),
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=timeout_provider,
            llm_request_timeout_sec=0.05,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(profile_id="default", session_id="s-llm-timeout", message="hello")

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "LLM request timed out before planning could complete."

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in events]
        assert "llm.call.start" in event_types
        assert "llm.call.timeout" in event_types

    await engine.dispose()


async def test_plan_only_turn_exposes_read_only_tools_and_reasoning_budget(tmp_path: Path) -> None:
    """Plan-only turns should hide mutating tools and forward a bounded planning budget."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_plan_only.db")
    provider = MockLLMProvider([LLMResponse.final("1. Inspect\n2. Implement\n3. Verify")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            llm_max_iterations=50,
            llm_request_timeout_sec=30.0,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-plan-only",
            message="Implement a new channel adapter and update docs.",
            context_overrides=TurnContextOverrides(
                planning_mode="plan_only",
                thinking_level="high",
            ),
        )

        assert result.envelope.action == "finalize"
        assert provider.requests
        request = provider.requests[0]
        visible_names = {tool.name for tool in request.available_tools}
        assert "file.write" not in visible_names
        assert "app.run" not in visible_names
        assert "file.read" in visible_names
        assert request.reasoning_effort == "high"
        assert request.request_timeout_sec == 45.0

    await engine.dispose()


async def test_execution_planning_auto_injects_overlay_for_complex_tasks(tmp_path: Path) -> None:
    """Auto planning mode should inject internal execution-planning guidance for complex tasks."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_execution_planning_auto.db")
    provider = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            chat_planning_mode="auto",
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-execution-planning-auto",
            message="Implement channel routing and update the docs after that.",
        )

        assert result.envelope.action == "finalize"
        assert provider.requests
        request = provider.requests[0]
        assert "# Execution Planning" in request.context
        assert "derive a concise internal step-by-step plan" in request.context

        plan_event = (
            (
                await session.execute(
                    select(RunlogEvent)
                    .where(RunlogEvent.event_type == "turn.plan")
                    .order_by(RunlogEvent.id.desc())
                )
            )
            .scalars()
            .first()
        )
        assert plan_event is not None
        payload = json.loads(plan_event.payload_json)
        assert payload["chat_planning_mode"] == "auto"
        assert payload["execution_planning_enabled"] is True

    await engine.dispose()


async def test_execution_planning_override_off_disables_runtime_overlay(tmp_path: Path) -> None:
    """Per-turn execution planning override should disable internal planning even when profile mode is on."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_execution_planning_override_off.db")
    provider = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            chat_planning_mode="on",
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-execution-planning-off",
            message="Implement channel routing and update the docs after that.",
            context_overrides=TurnContextOverrides(execution_planning_mode="off"),
        )

        assert result.envelope.action == "finalize"
        assert provider.requests
        request = provider.requests[0]
        assert "# Execution Planning" not in request.context

        plan_event = (
            (
                await session.execute(
                    select(RunlogEvent)
                    .where(RunlogEvent.event_type == "turn.plan")
                    .order_by(RunlogEvent.id.desc())
                )
            )
            .scalars()
            .first()
        )
        assert plan_event is not None
        payload = json.loads(plan_event.payload_json)
        assert payload["chat_planning_mode"] == "off"
        assert payload["execution_planning_enabled"] is False

    await engine.dispose()


async def test_llm_history_replays_previous_chat_turns(tmp_path: Path) -> None:
    """Second turn should include prior user+assistant messages in provider history."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_history_replay.db")
    scripted = MockLLMProvider([LLMResponse.final("first"), LLMResponse.final("second")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            llm_history_turns=8,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        first = await loop.run_turn(profile_id="default", session_id="s-hist", message="hello one")
        second = await loop.run_turn(profile_id="default", session_id="s-hist", message="hello two")

        assert first.envelope.action == "finalize"
        assert second.envelope.action == "finalize"

    assert len(scripted.requests) == 2
    second_history = scripted.requests[1].history
    assert [msg.role for msg in second_history] == ["user", "assistant", "user"]
    assert second_history[0].content == "hello one"
    assert second_history[1].content == "first"
    assert second_history[2].content == "hello two"

    await engine.dispose()


async def test_llm_context_includes_recent_browser_state_from_prior_turn(tmp_path: Path) -> None:
    """Second turn should receive compact browser carryover in trusted runtime notes."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_browser_carryover.db")

    class _BrowserSnapshotTool(ToolBase):
        name = "browser.control"
        description = "Return one compact browser snapshot."
        parameters_model = ToolParameters

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            return ToolResult(
                ok=True,
                payload={
                    "action": "snapshot",
                    "url": "https://example.com",
                    "title": "Example title",
                    "snapshot": {
                        "headings": ["Hero", "Pricing"],
                        "buttons": ["Start now"],
                        "text": "Hero section with pricing and CTA button",
                    },
                },
            )

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response([ToolCallRequest(name="browser.control", params={})]),
            LLMResponse.final("first done"),
            LLMResponse.final("second done"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_BrowserSnapshotTool()]),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        first = await loop.run_turn(
            profile_id="default",
            session_id="s-browser-carryover",
            message="open site and inspect it",
        )
        second = await loop.run_turn(
            profile_id="default",
            session_id="s-browser-carryover",
            message="continue in browser and review pricing",
        )

        assert first.envelope.action == "finalize"
        assert second.envelope.action == "finalize"

    assert len(scripted.requests) == 3
    second_request = scripted.requests[2]
    assert "Trusted browser carryover from recent turns" in second_request.context
    assert "Last known page URL: https://example.com" in second_request.context
    assert "Headings: Hero, Pricing" in second_request.context

    await engine.dispose()


async def test_llm_runtime_budget_stops_long_multi_iteration_turns(tmp_path: Path) -> None:
    """Total wall-clock budget should stop iterative turns before max_iterations when time is spent across iterations."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_runtime_budget.db")
    settings = settings.model_copy(update={"llm_shared_request_min_interval_ms": 0})

    class _SlowTool(ToolBase):
        name = "debug.loopslow"
        description = "Sleep briefly to consume turn budget."
        parameters_model = ToolParameters

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            _ = ctx, params
            await asyncio.sleep(0.1)
            return ToolResult(ok=True, payload={"ok": True})

    class _LoopingProvider(BaseLLMProvider):
        def __init__(self) -> None:
            self.requests: list[object] = []

        async def complete(self, request: object) -> LLMResponse:
            self.requests.append(request)
            return LLMResponse.tool_calls_response([ToolCallRequest(name="debug.loopslow", params={})])

    provider = _LoopingProvider()

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_SlowTool()]),
            llm_provider=provider,
            llm_request_timeout_sec=0.2,
            llm_max_iterations=50,
            llm_execution_budget_low_sec=0.15,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-runtime-budget",
            message="keep working until done",
            context_overrides=TurnContextOverrides(thinking_level="low"),
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message.startswith("finalized: runtime_budget_reached")

    assert len(provider.requests) < 50
    await engine.dispose()


async def test_llm_history_uses_compacted_session_summary(tmp_path: Path) -> None:
    """Fifth turn should receive compacted summary plus only recent raw turns."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_history_compaction.db")
    scripted = MockLLMProvider(
        [
            LLMResponse.final("one"),
            LLMResponse.final("two"),
            LLMResponse.final("three"),
            LLMResponse.final("four"),
            LLMResponse.final("five"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            llm_history_turns=2,
            session_compaction_enabled=True,
            session_compaction_trigger_turns=3,
            session_compaction_keep_recent_turns=2,
            session_compaction_max_chars=2000,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        for index in range(4):
            result = await loop.run_turn(
                profile_id="default",
                session_id="s-compact",
                message=f"hello {index + 1}",
            )
            assert result.envelope.action == "finalize"

        compaction = await session.get(
            ChatSessionCompaction,
            {"session_id": "s-compact", "profile_id": "default"},
        )
        assert compaction is not None
        assert compaction.compacted_until_turn_id == 2
        assert compaction.source_turn_count == 2

        result = await loop.run_turn(
            profile_id="default",
            session_id="s-compact",
            message="hello 5",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 5
    fifth_history = scripted.requests[4].history
    assert fifth_history[0].role == "system"
    assert fifth_history[0].content is not None
    assert "Compacted through turn 2." in fifth_history[0].content
    assert [msg.role for msg in fifth_history[1:]] == ["user", "assistant", "user", "assistant", "user"]
    assert fifth_history[1].content == "hello 3"
    assert fifth_history[2].content == "three"
    assert fifth_history[3].content == "hello 4"
    assert fifth_history[4].content == "four"
    assert fifth_history[5].content == "hello 5"

    await engine.dispose()


async def test_llm_secret_output_is_blocked_and_redacted(tmp_path: Path) -> None:
    """Secret-like assistant output should produce block envelope and redacted persistence."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_secret_output.db")
    scripted = MockLLMProvider([LLMResponse.final("your password is qwerty")])

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
        result = await loop.run_turn(profile_id="default", session_id="s-llm-secret", message="hello")

        assert result.envelope.action == "block"
        assert result.envelope.blocked_reason == "security_secret_output_blocked"
        assert "qwerty" not in result.envelope.message

        turns = (await session.execute(select(ChatTurn))).scalars().all()
        assert len(turns) == 1
        assert "qwerty" not in turns[0].assistant_message
        assert "blocked" in turns[0].assistant_message.lower()

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in events]
        assert "turn.block" in event_types
        assert event_types.index("turn.block") < event_types.index("turn.finalize")
        assert "qwerty" not in "\n".join(event.payload_json for event in events)

        finalize_payload = json.loads(
            [event.payload_json for event in events if event.event_type == "turn.finalize"][0]
        )
        assert "assistant_message" in finalize_payload
        assert "qwerty" not in json.dumps(finalize_payload)
        assert "[REDACTED]" not in finalize_payload.keys()

    await engine.dispose()


async def test_llm_loop_stops_on_max_iterations(tmp_path: Path) -> None:
    """LLM loop should emit deterministic finalize when max iterations is reached."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_limit.db")

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response([ToolCallRequest(name="debug.echo", params={"message": "1"})]),
            LLMResponse.tool_calls_response([ToolCallRequest(name="debug.echo", params={"message": "2"})]),
            LLMResponse.tool_calls_response([ToolCallRequest(name="debug.echo", params={"message": "3"})]),
        ]
    )

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
        result = await loop.run_turn(profile_id="default", session_id="s-max", message="hello")

        assert result.envelope.message == "finalized: max_iterations_reached (2)"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        tool_calls_count = sum(1 for event in events if event.event_type == "tool.call")
        assert tool_calls_count == 2

    await engine.dispose()


async def test_llm_loop_honors_policy_max_iterations(tmp_path: Path) -> None:
    """Profile policy max_iterations_main should cap configured LLM loop iterations."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_policy_limit.db")
    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response([ToolCallRequest(name="debug.echo", params={"message": "1"})]),
            LLMResponse.tool_calls_response([ToolCallRequest(name="debug.echo", params={"message": "2"})]),
        ]
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.max_iterations_main = 1
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(profile_id="default", session_id="s-policy-limit", message="hello")

        assert result.envelope.message == "finalized: max_iterations_reached (1)"
        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        tool_calls_count = sum(1 for event in events if event.event_type == "tool.call")
        assert tool_calls_count == 1

    await engine.dispose()


async def test_llm_high_thinking_level_no_longer_stops_at_ten_iterations(tmp_path: Path) -> None:
    """High thinking should honor the raised runtime cap instead of stopping after 10 loops."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_high_limit.db")
    scripted = MockLLMProvider(
        [
            *[
                LLMResponse.tool_calls_response(
                    [ToolCallRequest(name="debug.echo", params={"message": str(index)})]
                )
                for index in range(1, 12)
            ],
            LLMResponse.final("done after 11 tool calls"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=DEFAULT_LLM_MAX_ITERATIONS,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-high-limit",
            message="hello",
            context_overrides=TurnContextOverrides(thinking_level="high"),
        )

        # Assert
        assert result.envelope.message == "done after 11 tool calls"
        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        tool_calls_count = sum(1 for event in events if event.event_type == "tool.call")
        assert tool_calls_count == 11

    await engine.dispose()


async def test_llm_tool_calls_respect_profile_policy(tmp_path: Path) -> None:
    """Policy allow/deny must be applied to tool calls generated by LLM."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_policy.db")

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [ToolCallRequest(name="debug.echo", params={"message": "should block"})]
            ),
            LLMResponse.final("done"),
        ]
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.denied_tools_json = '["debug.echo"]'
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(profile_id="default", session_id="s-policy", message="hello")

        # Assert
        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["error_code"] == "tool_not_allowed_in_turn"
        assert result.envelope.action == "ask_question"
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch["tool_name"] == "debug.echo"
        assert result.envelope.spec_patch["question_kind"] == "tool_not_allowed_in_turn"

    assert len(scripted.requests) == 1
    await engine.dispose()


async def test_llm_remote_host_phrasing_stays_in_normal_execution_flow(tmp_path: Path) -> None:
    """Explicit remote-host phrasing should still go through normal LLM execution flow."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_remote_target_flow.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-remote-target-flow",
            message="подключись по ssh на remote server и установи nginx",
        )

        # Assert
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    await engine.dispose()


async def test_llm_keeps_local_ssh_package_tasks_in_normal_execution_flow(tmp_path: Path) -> None:
    """Standalone local SSH package tasks should not be misclassified as remote execution."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_local_ssh_package_task.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-local-ssh-package",
            message="install ssh and configure ssh keys on this machine",
        )

        # Assert
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    await engine.dispose()


async def test_llm_stops_repeating_missing_file_reads_without_write_access(tmp_path: Path) -> None:
    """Loop should stop early instead of guessing file names forever on repeated missing reads."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_missing_file_reads.db")
    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response([ToolCallRequest(name="file.read", params={"path": "missing.txt"})]),
            LLMResponse.tool_calls_response([ToolCallRequest(name="file.read", params={"path": "missing.txt"})]),
            LLMResponse.tool_calls_response([ToolCallRequest(name="file.read", params={"path": "missing.txt"})]),
        ]
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = json.dumps(["file.read"], ensure_ascii=True, sort_keys=True)
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=10,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(profile_id="default", session_id="s-missing-read", message="read missing")

        assert (
            result.envelope.message
            == "The requested file could not be found, and the current tool surface does not allow creating or editing files. Provide an existing path or enable file write access."
        )

    assert len(scripted.requests) == 3
    await engine.dispose()


async def test_llm_request_tools_follow_policy_allowlist(tmp_path: Path) -> None:
    """LLM context must expose only policy-visible tools to planner."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_policy_tools_allow.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = json.dumps(["debug.echo"], ensure_ascii=True, sort_keys=True)
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
        )
        visible = tool_surface.visible_tools
        tool_names = {item.name for item in visible}
        assert tool_names == {"debug.echo"}

    await engine.dispose()


async def test_llm_request_tools_fail_closed_on_invalid_policy_json(tmp_path: Path) -> None:
    """Invalid policy JSON should hide all LLM-visible tools."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_policy_tools_invalid.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = "{not-json"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
        )
        visible = tool_surface.visible_tools
        assert visible == ()

    await engine.dispose()


async def test_llm_cli_approval_surface_fails_closed_on_invalid_deny_policy_json(
    tmp_path: Path,
) -> None:
    """CLI approval surface should fail closed when deny rules are invalid."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_cli_approval_invalid_deny.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = json.dumps(["debug.echo"], ensure_ascii=True, sort_keys=True)
        policy.denied_tools_json = "{not-json"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
            approved_tool_names=("bash.exec",),
            cli_approval_surface_enabled=True,
        )

        assert tool_surface.visible_tools == ()
        assert tool_surface.executable_tool_names == ()
        assert tool_surface.approval_required_tool_names == ()

    await engine.dispose()


async def test_llm_visible_tools_include_cli_approval_surface_for_afk_chat(tmp_path: Path) -> None:
    """Trusted afk chat should expose curated approval-required tools even outside policy allowlist."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_cli_approval_surface.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = json.dumps(["debug.echo"], ensure_ascii=True, sort_keys=True)
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
            cli_approval_surface_enabled=True,
        )
        visible = tool_surface.visible_tools
        by_name = {item.name: item for item in visible}
        assert "debug.echo" in by_name
        assert "bash.exec" in by_name
        assert "file.read" in by_name
        assert by_name["bash.exec"].requires_confirmation is True
        assert by_name["file.read"].requires_confirmation is True

    await engine.dispose()


async def test_llm_visible_tools_include_explicitly_approved_tool_without_bypassing_deny_rules(
    tmp_path: Path,
) -> None:
    """Trusted CLI-approved tools should become visible to replanning without bypassing denies."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_approved_tools_visible.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_enabled = True
        policy.allowed_tools_json = json.dumps(["debug.echo"], ensure_ascii=True, sort_keys=True)
        policy.denied_tools_json = json.dumps(["file.read"], ensure_ascii=True, sort_keys=True)
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
            approved_tool_names=("bash.exec", "file.read"),
        )
        visible = tool_surface.visible_tools
        tool_names = {item.name for item in visible}
        assert "debug.echo" in tool_names
        assert "bash.exec" in tool_names
        assert "file.read" not in tool_names

    await engine.dispose()


async def test_llm_suggests_credentials_request_after_empty_credentials_list(tmp_path: Path) -> None:
    """Runtime should nudge secure credential collection after empty credentials.list results."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_credentials_followup.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        followup = _tool_followup_policy(settings).determine(
            tool_calls=[
                ToolCall(
                    name="credentials.list",
                    params={"app_name": "imap"},
                )
            ],
            tool_results=[ToolResult(ok=True, payload={"bindings": []})],
            visible_tool_names={"credentials.list", "credentials.request", "app.run"},
            consecutive_missing_file_reads=0,
            profile_id="default",
        )

        assert followup.final_message is None
        assert followup.history_prompt is not None
        assert "credentials.request" in followup.history_prompt
        assert "imap_host" in followup.history_prompt

    await engine.dispose()


async def test_llm_suggests_bash_session_followup_for_live_shell(tmp_path: Path) -> None:
    """Runtime should nudge the model to continue a live `bash.exec` session."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_bash_followup.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        followup = _tool_followup_policy(settings).determine(
            tool_calls=[
                ToolCall(
                    name="bash.exec",
                    params={"cmd": "npx vibe-kanban", "yield_time_ms": 500},
                )
            ],
            tool_results=[
                ToolResult(
                    ok=True,
                    payload={
                        "running": True,
                        "session_id": "bash-live-1",
                        "stdout": "Need to install the following packages:\nvibe-kanban@0.1.30\nOk to proceed? (y) ",
                    },
                )
            ],
            visible_tool_names={"bash.exec"},
            consecutive_missing_file_reads=0,
            profile_id="default",
        )

        assert followup.final_message is None
        assert followup.history_prompt is not None
        assert "session_id=bash-live-1" in followup.history_prompt
        assert "`chars`" in followup.history_prompt
        assert "Do not finalize" in followup.history_prompt

    await engine.dispose()


async def test_llm_file_search_followup_ignores_mismatched_result_lengths(tmp_path: Path) -> None:
    """Mismatched tool call/result lengths should not crash file-search followup hints."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_file_search_lengths.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        followup = _tool_followup_policy(settings).determine(
            tool_calls=[
                ToolCall(
                    name="file.search",
                    params={"path": "file.txt", "query": "hello"},
                )
            ],
            tool_results=[],
            visible_tool_names={"file.read", "file.search", "diffs.render"},
            consecutive_missing_file_reads=0,
            profile_id="default",
        )

        assert followup.final_message is None
        assert followup.history_prompt is None

    await engine.dispose()


async def test_llm_browser_target_closed_followup_prompts_reopen(tmp_path: Path) -> None:
    """Runtime should inject a reopen hint after target-closed browser failures."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_browser_followup.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        followup = _tool_followup_policy(settings).determine(
            tool_calls=[ToolCall(name="browser.control", params={"action": "open"})],
            tool_results=[
                ToolResult.error(
                    error_code="browser_action_failed",
                    reason="TargetClosedError: Page.goto: Target page, context or browser has been closed",
                    metadata={
                        "browser_error_class": "browser_target_closed",
                        "retryable": True,
                        "requires_session_reset": True,
                        "suggested_next_action": "reopen_session",
                        "session_state": "dead",
                    },
                )
            ],
            visible_tool_names={"browser.control"},
            consecutive_missing_file_reads=0,
            profile_id="default",
        )

        assert followup.final_message is None
        assert followup.history_prompt is not None
        assert "action='open'" in followup.history_prompt
        assert "session as dead" in followup.history_prompt

    await engine.dispose()


async def test_llm_browser_invalid_followup_prompts_supported_target_fields(tmp_path: Path) -> None:
    """Runtime should suggest supported semantic browser fields after invalid browser requests."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_browser_invalid_followup.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        followup = _tool_followup_policy(settings).determine(
            tool_calls=[ToolCall(name="browser.control", params={"action": "click"})],
            tool_results=[
                ToolResult.error(
                    error_code="browser_invalid",
                    reason="click action requires one browser target",
                    metadata={
                        "browser_error_class": "browser_invalid_request",
                        "retryable": False,
                        "requires_session_reset": False,
                        "suggested_next_action": "fix_request",
                        "session_state": "unknown",
                    },
                )
            ],
            visible_tool_names={"browser.control"},
            consecutive_missing_file_reads=0,
            profile_id="default",
        )

        assert followup.final_message is None
        assert followup.history_prompt is not None
        assert "`label`" in followup.history_prompt
        assert "`target_text`" in followup.history_prompt
        assert "click action requires one browser target" in followup.history_prompt

    await engine.dispose()
