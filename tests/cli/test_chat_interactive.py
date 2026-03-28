"""Tests for interactive chat progress rendering."""

from __future__ import annotations

from io import StringIO
import threading
import time
from typing import cast

from prompt_toolkit import PromptSession

from afkbot.cli.presentation.chat_interactive import InteractiveChatUX
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from tests.cli._rendering import strip_ansi


class _TTYBuffer(StringIO):
    """Minimal stdout double that behaves like a TTY for chat rendering tests."""

    def isatty(self) -> bool:
        return True


class _PromptStub:
    """Prompt stub for constructing InteractiveChatUX in unit tests."""

    def prompt(self, _prompt: object) -> str:
        raise AssertionError("prompt should not be used in progress rendering tests")


def _make_chat_ux(*, spinner_enabled: bool, tool_panel_frame_interval_sec: float = 0.12) -> InteractiveChatUX:
    return InteractiveChatUX(
        _prompt=cast(PromptSession[str], _PromptStub()),
        _spinner_enabled=spinner_enabled,
        _stop_event=threading.Event(),
        _lock=threading.Lock(),
        _tool_panel_frame_interval_sec=tool_panel_frame_interval_sec,
    )


def test_interactive_chat_skips_setup_thinking_event(monkeypatch) -> None:
    """Iteration-zero thinking should not print the extra bare setup line."""

    # Arrange
    stream = _TTYBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    ux = _make_chat_ux(spinner_enabled=False)
    ux.begin_agent_turn()
    before = stream.getvalue()
    setup_event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="thinking",
        iteration=0,
        tool_name=None,
        event_type="turn.progress",
    )
    visible_event = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.tick",
        payload={"elapsed_ms": 3000, "timeout_ms": 30000},
    )

    # Act
    ux.on_progress(setup_event)
    after_setup = stream.getvalue()
    ux.on_progress(visible_event)

    # Assert
    assert after_setup == before
    assert "[iter 1] thinking..." in stream.getvalue()


def test_interactive_chat_keeps_one_live_tool_panel_with_latest_ten_lines(monkeypatch) -> None:
    """TTY rendering should rewrite one transient tool panel instead of appending duplicates."""

    # Arrange
    stream = _TTYBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    ux = _make_chat_ux(spinner_enabled=True, tool_panel_frame_interval_sec=60.0)
    ux.begin_agent_turn()
    call_event = ProgressEvent(
        event_id=10,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    call_event.attach_tool_details(tool_call_params={"cmd": "apt-get install", "cwd": "."})
    progress_event = ProgressEvent(
        event_id=11,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={
            "preview_lines": [f"stdout | line-{index:02d}" for index in range(1, 13)],
            "stream": "mixed",
        }
    )

    # Act
    ux.on_progress(call_event)
    ux.on_progress(progress_event)
    output_after_first_progress = stream.getvalue()
    ux.on_progress(progress_event)

    # Assert
    assert ux._active_tool_status_line == "[iter 1] [#1] tool running: bash.exec"
    assert ux._active_tool_detail_lines == tuple(f"stdout | line-{index:02d}" for index in range(3, 13))
    assert "\033[2K" in output_after_first_progress
    assert stream.getvalue() == output_after_first_progress
    ux.stop_progress()


def test_interactive_chat_prints_final_result_after_clearing_live_panel(monkeypatch) -> None:
    """Tool completion should clear the transient panel and print one final completion line."""

    # Arrange
    stream = _TTYBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    ux = _make_chat_ux(spinner_enabled=True, tool_panel_frame_interval_sec=60.0)
    ux.begin_agent_turn()
    progress_event = ProgressEvent(
        event_id=20,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | installing package"], "stream": "mixed"}
    )
    result_event = ProgressEvent(
        event_id=21,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    result_event.attach_tool_details(tool_result={"ok": True, "payload": {"exit_code": 0}})

    # Act
    ux.on_progress(progress_event)
    ux.on_progress(result_event)

    # Assert
    assert ux._active_tool_block_lines == 0
    assert ux._active_tool_status_line is None
    assert ux._active_tool_detail_lines == ()
    assert "tool completed: bash.exec" in strip_ansi(stream.getvalue())


def test_interactive_chat_keeps_live_tool_result_open_until_final_result(monkeypatch) -> None:
    """Live bash session results should stay transient until the session actually exits."""

    # Arrange
    stream = _TTYBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    ux = _make_chat_ux(spinner_enabled=True, tool_panel_frame_interval_sec=60.0)
    ux.begin_agent_turn()
    call_event = ProgressEvent(
        event_id=30,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    call_event.attach_tool_details(tool_call_params={"cmd": "npx vibe-kanban"})
    progress_event = ProgressEvent(
        event_id=31,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | Need to install", "stdout | Ok to proceed? (y)"]}
    )
    live_result_event = ProgressEvent(
        event_id=32,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    live_result_event.attach_tool_details(
        tool_result={
            "ok": True,
            "payload": {
                "running": True,
                "session_id": "bash-live-1",
                "stdout": "Ok to proceed? (y)",
            },
        }
    )
    final_result_event = ProgressEvent(
        event_id=33,
        run_id=1,
        stage="tool_call",
        iteration=3,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    final_result_event.attach_tool_details(
        tool_result={"ok": True, "payload": {"exit_code": 0, "stdout": "installed"}}
    )

    # Act
    ux.on_progress(call_event)
    ux.on_progress(progress_event)
    ux.on_progress(live_result_event)
    live_block_lines = ux._active_tool_block_lines  # noqa: SLF001
    ux.on_progress(final_result_event)
    frozen_block_lines = ux._active_tool_block_lines  # noqa: SLF001

    # Assert
    assert live_block_lines > 0
    assert frozen_block_lines == 0
    stripped = strip_ansi(stream.getvalue())
    assert "tool running: bash.exec" in stripped
    assert "tool completed: bash.exec" in stripped


def test_interactive_chat_animates_live_tool_panel_header(monkeypatch) -> None:
    """Live tool panels should keep animating their header while the tool is running."""

    # Arrange
    stream = _TTYBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    ux = _make_chat_ux(spinner_enabled=True, tool_panel_frame_interval_sec=0.01)
    ux.begin_agent_turn()
    progress_event = ProgressEvent(
        event_id=40,
        run_id=1,
        stage="tool_call",
        iteration=3,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | waiting for apt"], "stream": "mixed"}
    )

    # Act
    ux.on_progress(progress_event)
    first_output = stream.getvalue()
    time.sleep(0.05)
    second_output = stream.getvalue()
    ux.stop_progress()

    # Assert
    assert second_output != first_output
    assert ux._tool_panel_thread is None or not ux._tool_panel_thread.is_alive()
    assert second_output.count("tool running: bash.exec") >= 2
