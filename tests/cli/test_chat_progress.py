"""Tests for chat progress presentation mapping and rendering."""

from __future__ import annotations

from afkbot.cli.presentation import (
    map_progress_event,
    render_progress_color,
    render_progress_detail,
    render_progress_event,
)
from afkbot.cli.presentation.progress_renderer import render_progress_detail_lines
from afkbot.services.agent_loop.progress_stream import ProgressEvent


def test_chat_progress_renderer_for_all_stages() -> None:
    """Renderer should provide deterministic short text for every canonical stage."""

    events: list[tuple[ProgressEvent, str]] = [
        (
            ProgressEvent(
                event_id=1,
                run_id=1,
                stage="thinking",
                iteration=2,
                tool_name=None,
                event_type="turn.progress",
            ),
            "thinking...",
        ),
        (
            ProgressEvent(
                event_id=2,
                run_id=1,
                stage="planning",
                iteration=2,
                tool_name=None,
                event_type="turn.progress",
            ),
            "planning...",
        ),
        (
            ProgressEvent(
                event_id=3,
                run_id=1,
                stage="tool_call",
                iteration=None,
                tool_name="debug.echo",
                event_type="tool.call",
            ),
            "● calling tool: debug.echo",
        ),
        (
            ProgressEvent(
                event_id=4,
                run_id=1,
                stage="subagent_wait",
                iteration=None,
                tool_name="subagent.wait",
                event_type="tool.call",
            ),
            "● waiting subagent: subagent.wait",
        ),
        (
            ProgressEvent(
                event_id=5,
                run_id=1,
                stage="done",
                iteration=None,
                tool_name=None,
                event_type="turn.finalize",
            ),
            "response ready",
        ),
        (
            ProgressEvent(
                event_id=6,
                run_id=1,
                stage="cancelled",
                iteration=None,
                tool_name=None,
                event_type="turn.cancel",
            ),
            "cancelled",
        ),
    ]

    for progress_event, expected_text in events:
        mapped = map_progress_event(progress_event)
        assert mapped is not None
        assert render_progress_event(mapped) == expected_text


def test_chat_progress_mapper_can_skip_blank_event_type() -> None:
    """Mapper may skip events with empty semantic event type."""

    mapped = map_progress_event(
        ProgressEvent(
            event_id=1,
            run_id=1,
            stage="thinking",
            iteration=None,
            tool_name=None,
            event_type="   ",
        )
    )
    assert mapped is None


def test_chat_progress_mapper_skips_generic_tool_stage_without_tool_name() -> None:
    """Mapper should skip generic tool/progress events without concrete tool name."""

    mapped = map_progress_event(
        ProgressEvent(
            event_id=2,
            run_id=1,
            stage="tool_call",
            iteration=1,
            tool_name=None,
            event_type="turn.progress",
        )
    )
    assert mapped is None


def test_chat_progress_detail_for_bash_tool_call() -> None:
    """Tool-call detail should include bash command and cwd in compact format."""

    event = ProgressEvent(
        event_id=7,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    event.attach_tool_details(
        tool_call_params={
            "cmd": "pwd",
            "cwd": ".",
            "timeout_sec": 15,
        }
    )

    assert render_progress_detail(event) == "params: cmd=pwd cwd=."


def test_chat_progress_detail_for_bash_tool_call_without_cwd() -> None:
    """Bash tool-call detail should omit cwd when value is absent."""

    event = ProgressEvent(
        event_id=71,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    event.attach_tool_details(tool_call_params={"cmd": "pwd"})

    assert render_progress_detail(event) == "params: cmd=pwd"


def test_chat_progress_detail_for_bash_session_poll_call() -> None:
    """Bash tool-call detail should show resumed session ids and stdin chars."""

    event = ProgressEvent(
        event_id=72,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    event.attach_tool_details(
        tool_call_params={
            "session_id": "bash-abc123",
            "chars": "y\n",
        }
    )

    assert render_progress_detail(event) == "params: session_id=bash-abc123 chars=y"


def test_chat_progress_detail_for_tool_progress_preview_lines() -> None:
    """Tool-progress detail should expose sanitized rolling preview lines."""

    # Arrange
    event = ProgressEvent(
        event_id=73,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    event.attach_tool_details(
        tool_progress={
            "preview_lines": [
                "stdout | one",
                "stderr | two\x1b]2;PWNED\x07",
            ],
            "stream": "mixed",
        }
    )

    # Act
    mapped = map_progress_event(event)
    detail_lines = render_progress_detail_lines(event)

    # Assert
    assert mapped is not None
    assert render_progress_event(mapped) == "● tool running: bash.exec"
    assert detail_lines == ("stdout | one", "stderr | two")
    assert render_progress_detail(event) == "stderr | two"


def test_chat_progress_detail_for_tool_error_result() -> None:
    """Tool-result detail should surface error code and reason."""

    event = ProgressEvent(
        event_id=8,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="app.run",
        event_type="tool.result",
    )
    event.attach_tool_details(
        tool_result={
            "ok": False,
            "error_code": "profile_policy_violation",
            "reason": "Network host is not allowed by policy: api.telegram.org",
        }
    )
    rendered = render_progress_detail(event)
    assert rendered is not None
    assert rendered.startswith("error=profile_policy_violation")


def test_chat_progress_detail_for_running_bash_session_result() -> None:
    """Running bash session results should report session id instead of exit_code=None."""

    event = ProgressEvent(
        event_id=81,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    event.attach_tool_details(
        tool_result={
            "ok": True,
            "payload": {
                "session_id": "bash-abc123",
                "running": True,
                "stdout": "Ok to proceed? (y)",
            },
        }
    )

    assert (
        render_progress_detail(event)
        == "session_id=bash-abc123 running=true stdout=Ok to proceed? (y)"
    )


def test_chat_progress_live_bash_session_result_renders_as_tool_running() -> None:
    """Intermediate live-session results should stay in the running state, not completed."""

    # Arrange
    event = ProgressEvent(
        event_id=82,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    event.attach_tool_details(
        tool_result={
            "ok": True,
            "payload": {
                "running": True,
                "session_id": "bash-abc123",
                "stdout": "Ok to proceed? (y)",
            },
        }
    )

    # Act
    mapped = map_progress_event(event)

    # Assert
    assert mapped is not None
    assert render_progress_event(mapped) == "● tool running: bash.exec"


def test_chat_progress_event_sanitizes_terminal_control_sequences() -> None:
    """Rendered status lines should strip terminal control characters from tool names."""

    event = ProgressEvent(
        event_id=9,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="app.run\x1b]2;PWNED\x07",
        event_type="tool.call",
    )
    mapped = map_progress_event(event)
    assert mapped is not None
    rendered = render_progress_event(mapped)
    assert "\x1b" not in rendered
    assert "PWNED" not in rendered
    assert "app.run" in rendered


def test_chat_progress_detail_sanitizes_terminal_control_sequences() -> None:
    """Detail line should not print raw escape/control sequences from tool payloads."""

    event = ProgressEvent(
        event_id=10,
        run_id=1,
        stage="tool_call",
        iteration=None,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    event.attach_tool_details(tool_call_params={"cmd": "echo \x1b]2;PWNED\x07", "cwd": "."})
    rendered = render_progress_detail(event)
    assert rendered is not None
    assert "\x1b" not in rendered
    assert "PWNED" not in rendered


def test_chat_progress_detail_for_llm_call_tick() -> None:
    """LLM call progress events should include elapsed/timeout details."""

    event = ProgressEvent(
        event_id=11,
        run_id=1,
        stage="thinking",
        iteration=3,
        tool_name=None,
        event_type="llm.call.tick",
        payload={"elapsed_ms": 6000, "timeout_ms": 30000},
    )
    assert render_progress_detail(event) == "llm=tick elapsed_ms=6000 timeout_ms=30000"


def test_chat_progress_event_for_context_compaction_steps() -> None:
    """Automatic context compaction should render as dedicated visible system steps."""

    start_event = ProgressEvent(
        event_id=600,
        run_id=1,
        stage="thinking",
        iteration=2,
        tool_name=None,
        event_type="llm.call.compaction_start",
        payload={"attempt": 1, "error_detail": "context window exceeded"},
    )
    done_event = ProgressEvent(
        event_id=601,
        run_id=1,
        stage="thinking",
        iteration=2,
        tool_name=None,
        event_type="llm.call.compaction_done",
        payload={
            "attempt": 1,
            "summary_strategy": "hybrid_llm_v1",
            "history_messages_before": 9,
            "history_messages_after": 4,
        },
    )

    mapped_start = map_progress_event(start_event)
    mapped_done = map_progress_event(done_event)

    assert mapped_start is not None
    assert mapped_done is not None
    assert render_progress_event(mapped_start) == "----- Automatic context compaction -----"
    assert render_progress_event(mapped_done) == "----- Context automatically compacted -----"
    assert render_progress_detail(start_event) == "attempt=1 provider=context window exceeded"
    assert render_progress_detail(done_event) == "attempt=1 summary=hybrid_llm_v1 history=9->4"


def test_chat_progress_detail_for_llm_call_start_includes_reasoning_and_tool_count() -> None:
    """LLM start detail should expose reasoning budget and visible tool count."""

    event = ProgressEvent(
        event_id=111,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.start",
        payload={
            "timeout_ms": 45000,
            "reasoning_effort": "high",
            "available_tool_names": ["file.read", "file.search", "web.search"],
        },
    )

    assert render_progress_detail(event) == "llm=start timeout_ms=45000 reasoning=high visible_tools=3"


def test_chat_progress_detail_for_turn_plan_includes_planning_metadata() -> None:
    """Planning event detail should expose plan mode, thinking level, and tool surface."""

    event = ProgressEvent(
        event_id=112,
        run_id=1,
        stage="planning",
        iteration=0,
        tool_name=None,
        event_type="turn.plan",
        payload={
            "planning_mode": "plan_only",
            "thinking_level": "very_high",
            "tool_access_mode": "read_only",
            "available_tools_after_filter": ["file.read", "file.search"],
        },
    )

    assert render_progress_detail(event) == "mode=plan_only thinking=very_high tools=read_only visible_tools=2"


def test_chat_progress_detail_for_turn_plan_includes_selected_skills() -> None:
    """Planning detail should stay hidden when plan-only mode is not active."""

    event = ProgressEvent(
        event_id=113,
        run_id=1,
        stage="planning",
        iteration=0,
        tool_name=None,
        event_type="turn.plan",
        payload={
            "planning_mode": "off",
            "thinking_level": "high",
            "tool_access_mode": "default",
            "selected_skill_names": ["doc", "file-ops"],
            "available_tools_after_filter": [
                "file.read",
                "file.write",
                "file.edit",
                "file.list",
                "diffs.render",
            ],
        },
    )

    assert render_progress_detail(event) is None


def test_chat_progress_color_scheme_for_key_statuses() -> None:
    """Key progress stages should keep the agreed terminal colors."""

    cases: list[tuple[ProgressEvent, str]] = [
        (
            ProgressEvent(
                event_id=12,
                run_id=1,
                stage="thinking",
                iteration=None,
                tool_name=None,
                event_type="turn.progress",
            ),
            "\033[94m",
        ),
        (
            ProgressEvent(
                event_id=13,
                run_id=1,
                stage="planning",
                iteration=None,
                tool_name=None,
                event_type="turn.progress",
            ),
            "\033[95m",
        ),
        (
            ProgressEvent(
                event_id=14,
                run_id=1,
                stage="thinking",
                iteration=1,
                tool_name=None,
                event_type="turn.progress",
            ),
            "\033[93m",
        ),
        (
            ProgressEvent(
                event_id=15,
                run_id=1,
                stage="done",
                iteration=None,
                tool_name=None,
                event_type="turn.finalize",
            ),
            "\033[92m",
        ),
        (
            ProgressEvent(
                event_id=16,
                run_id=1,
                stage="tool_call",
                iteration=1,
                tool_name="bash.exec",
                event_type="tool.call",
            ),
            "\033[93m",
        ),
        (
            ProgressEvent(
                event_id=17,
                run_id=1,
                stage="tool_call",
                iteration=1,
                tool_name="bash.exec",
                event_type="tool.result",
                payload={"result": {"ok": True, "payload": {"exit_code": 0}}},
            ),
            "\033[92m",
        ),
        (
            ProgressEvent(
                event_id=18,
                run_id=1,
                stage="tool_call",
                iteration=1,
                tool_name="bash.exec",
                event_type="tool.result",
                payload={"result": {"ok": False, "error_code": "tool_error"}},
            ),
            "\033[91m",
        ),
    ]

    for progress_event, expected_color in cases:
        if progress_event.event_type == "tool.result":
            progress_event.attach_tool_details(
                tool_result=progress_event.payload.get("result")
                if isinstance(progress_event.payload.get("result"), dict)
                else None
            )
        mapped = map_progress_event(progress_event)
        assert mapped is not None
        assert render_progress_color(mapped, progress_event=progress_event) == expected_color
