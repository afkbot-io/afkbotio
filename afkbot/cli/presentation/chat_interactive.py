"""Interactive chat UX helpers for readable progress narration."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import cast

from prompt_toolkit import PromptSession

from afkbot.cli.presentation.chat_style import AFK_AGENT_HEADER, CHAT_PROMPT
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.cli.presentation.progress_mapper import is_live_tool_result
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState, reduce_progress_event
from afkbot.cli.presentation.progress_renderer import render_progress_detail_lines
from afkbot.services.agent_loop.progress_stream import ProgressEvent

_MAX_TOOL_PANEL_LINES = 10

_DOT_FRAMES: tuple[str, ...] = (".  ", ".. ", "...")


@dataclass(slots=True)
class InteractiveChatUX:
    """Stateful interactive UX helper for chat REPL."""

    _prompt: PromptSession[str]
    _spinner_enabled: bool
    _stop_event: threading.Event
    _lock: threading.Lock
    _spinner_thread: threading.Thread | None = None
    _active_label: str | None = None
    _active_color: str = "\033[94m"
    _last_render_width: int = 0
    _prompt_count: int = 0
    _timeline_state: ProgressTimelineState = field(default_factory=ProgressTimelineState)
    _spinner_generation: int = 0
    _tool_panel_stop_event: threading.Event = field(default_factory=threading.Event)
    _tool_panel_thread: threading.Thread | None = None
    _tool_panel_generation: int = 0
    _tool_panel_frame_index: int = 0
    _tool_panel_frame_interval_sec: float = 0.08
    _active_tool_block_lines: int = 0
    _active_tool_status_line: str | None = None
    _active_tool_color: str = "\033[94m"
    _active_tool_detail_lines: tuple[str, ...] = ()
    _prompt_read_active: bool = False

    @classmethod
    def create(
        cls,
        *,
        prompt_session: PromptSession[str] | None = None,
    ) -> InteractiveChatUX:
        """Create configured UX object with default prompt session."""

        return cls(
            _prompt=cast(PromptSession[str], prompt_session or PromptSession()),
            _spinner_enabled=supports_interactive_tty(),
            _stop_event=threading.Event(),
            _lock=threading.Lock(),
            _timeline_state=ProgressTimelineState(),
        )

    def read_user_input(self) -> str:
        """Read one interactive prompt line using styled `you >` prefix."""

        self._prompt_count += 1
        if self._prompt_count > 1:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if not self._spinner_enabled:
            return input("you > ")
        return self._prompt.prompt(CHAT_PROMPT)

    def set_prompt_read_active(self, active: bool) -> None:
        """Record whether prompt-toolkit currently owns the terminal input line."""

        self._prompt_read_active = active

    def begin_agent_turn(self) -> None:
        """Start one visual assistant turn block."""

        self.stop_progress()
        self._timeline_state = ProgressTimelineState()
        sys.stdout.write(f"\n{AFK_AGENT_HEADER}\n")
        sys.stdout.flush()

    def on_progress(self, event: ProgressEvent) -> None:
        """Render progress updates with stage-aware spinner and status lines."""

        if self._should_skip_event(event):
            return
        if self._should_fold_into_spinner(event):
            return
        self._timeline_state, frame = reduce_progress_event(self._timeline_state, event)
        if frame is None:
            return
        color = frame.color

        if frame.separator_before:
            self._print_group_separator()

        if frame.stop_spinner:
            self._stop_spinner()

        if self._handle_live_tool_frame(event=event, status_line=frame.status_line, color=color):
            return

        if frame.spinner_label is not None:
            self._start_or_update(frame.spinner_label, color)
            return

        self.stop_progress()
        if frame.status_line is not None:
            self._print_status_line(frame.status_line, color)
        if frame.detail_line is not None:
            self._print_detail_line(frame.detail_line)

    def stop_progress(self) -> None:
        """Stop spinner and clear transient progress line."""

        self._stop_spinner()
        self._stop_tool_panel(clear=True)

    def _stop_spinner(self) -> None:
        with self._lock:
            thread = self._spinner_thread
            self._spinner_thread = None
            self._active_label = None
            self._active_color = "\033[94m"
            self._spinner_generation += 1
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=0.5)
        if self._spinner_enabled:
            self._clear_line()

    def _should_skip_event(self, event: ProgressEvent) -> bool:
        """Drop setup-only thinking noise and non-interactive live tool heartbeats."""

        if event.stage == "thinking" and (event.iteration is None or event.iteration <= 0):
            return True
        if not self._spinner_enabled and event.event_type == "tool.progress":
            return True
        return False

    def _handle_live_tool_frame(
        self,
        *,
        event: ProgressEvent,
        status_line: str | None,
        color: str,
    ) -> bool:
        """Render one active tool block in place instead of printing separate start/result lines."""

        if not self._spinner_enabled:
            return False
        if self._prompt_read_active:
            return False
        if event.stage != "tool_call":
            return False
        if event.event_type not in {"tool.call", "tool.progress", "tool.result"}:
            return False
        live_result = is_live_tool_result(event)
        if event.event_type == "tool.result" and not live_result:
            self._stop_tool_panel(clear=True)
            return False
        if status_line is None:
            return False

        panel_status_line = self._tool_panel_status_line(status_line)
        detail_lines = render_progress_detail_lines(event) or self._active_tool_detail_lines
        bounded_detail_lines = tuple(detail_lines[-_MAX_TOOL_PANEL_LINES:])
        if (
            panel_status_line == self._active_tool_status_line
            and bounded_detail_lines == self._active_tool_detail_lines
        ):
            return True
        self._stop_spinner()
        self._start_or_update_tool_panel(
            status_line=panel_status_line,
            detail_lines=bounded_detail_lines,
            color=color,
        )
        return True

    @staticmethod
    def _tool_panel_status_line(status_line: str) -> str:
        """Normalize tool panel wording so one tool owns one transient running block."""

        if "calling tool" in status_line:
            return status_line.replace("calling tool", "tool running", 1)
        if "tool completed" in status_line:
            return status_line.replace("tool completed", "tool running", 1)
        return status_line

    def _start_or_update_tool_panel(
        self,
        *,
        status_line: str,
        detail_lines: tuple[str, ...],
        color: str,
    ) -> None:
        """Render or refresh the active tool panel and keep its animation loop alive."""

        with self._lock:
            self._active_tool_status_line = status_line
            self._active_tool_color = color
            self._active_tool_detail_lines = tuple(detail_lines[-_MAX_TOOL_PANEL_LINES:])
            self._render_tool_block_locked(frame_index=self._tool_panel_frame_index)
            self._tool_panel_frame_index += 1
            if self._tool_panel_thread is not None and self._tool_panel_thread.is_alive():
                return
            self._tool_panel_generation += 1
            generation = self._tool_panel_generation
            self._tool_panel_stop_event.clear()
            self._tool_panel_thread = threading.Thread(
                target=self._tool_panel_loop,
                args=(generation,),
                name="afk-chat-tool-panel",
                daemon=True,
            )
            self._tool_panel_thread.start()

    def _tool_panel_loop(self, generation: int) -> None:
        """Animate the active tool panel header while the tool remains in-flight."""

        while not self._tool_panel_stop_event.wait(self._tool_panel_frame_interval_sec):
            with self._lock:
                if generation != self._tool_panel_generation:
                    break
                if self._active_tool_status_line is None:
                    break
                self._render_tool_block_locked(frame_index=self._tool_panel_frame_index)
                self._tool_panel_frame_index += 1

    def _stop_tool_panel(self, *, clear: bool) -> None:
        """Stop the live tool panel animation and optionally clear the panel block."""

        with self._lock:
            thread = self._tool_panel_thread
            self._tool_panel_thread = None
            self._tool_panel_generation += 1
            self._tool_panel_frame_index = 0
            self._tool_panel_stop_event.set()
        if thread is not None:
            thread.join(timeout=0.5)
        if clear:
            with self._lock:
                self._clear_active_tool_block_locked()

    def _render_tool_block_locked(self, *, frame_index: int) -> None:
        """Replace the currently active tool panel while holding the render lock."""

        if self._active_tool_status_line is None:
            return
        rendered_status = self._animated_tool_status_line(
            status_line=self._active_tool_status_line,
            frame_index=frame_index,
        )
        rendered_lines = [f"  {self._active_tool_color}\033[1m{rendered_status}\033[0m"]
        rendered_lines.extend(f"    \033[90m{line}\033[0m" for line in self._active_tool_detail_lines)

        self._erase_active_tool_block_locked()
        for line in rendered_lines:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()

        self._active_tool_block_lines = len(rendered_lines)

    @staticmethod
    def _animated_tool_status_line(*, status_line: str, frame_index: int) -> str:
        """Attach a small loader frame to the active tool header."""

        frame = _DOT_FRAMES[frame_index % len(_DOT_FRAMES)]
        return f"{status_line} {frame}"

    def _clear_active_tool_block_locked(self) -> None:
        """Clear the current tool block while the render lock is already held."""

        if not self._spinner_enabled or self._active_tool_block_lines <= 0:
            self._reset_active_tool_panel_state_locked()
            return

        self._erase_active_tool_block_locked()
        self._reset_active_tool_panel_state_locked()

    def _erase_active_tool_block_locked(self) -> None:
        """Erase active tool panel lines without dropping the in-memory panel state."""

        if not self._spinner_enabled or self._active_tool_block_lines <= 0:
            return

        line_count = self._active_tool_block_lines
        sys.stdout.write(f"\033[{line_count}F")
        for index in range(line_count):
            sys.stdout.write("\033[2K")
            if index < line_count - 1:
                sys.stdout.write("\033[1E")
        if line_count > 1:
            sys.stdout.write(f"\033[{line_count - 1}F")
        sys.stdout.flush()

    def _reset_active_tool_panel_state_locked(self) -> None:
        """Reset in-memory tool panel state while the render lock is already held."""

        self._active_tool_block_lines = 0
        self._active_tool_status_line = None
        self._active_tool_color = "\033[94m"
        self._active_tool_detail_lines = ()
        self._tool_panel_frame_index = 0

    def _should_fold_into_spinner(self, event: ProgressEvent) -> bool:
        """Hide noisy LLM heartbeat frames while the long-lived spinner is active."""

        if not self._spinner_enabled or self._active_label is None:
            return False
        if event.stage != "thinking":
            return False
        return event.event_type in {"llm.call.start", "llm.call.tick"}

    def _start_or_update(self, label: str, color: str) -> None:
        if not self._spinner_enabled or self._prompt_read_active:
            if self._active_label == label:
                return
            self._active_label = label
            self._active_color = color
            self._print_status_line(f"{label}...", color)
            return
        with self._lock:
            self._active_label = label
            self._active_color = color
            if self._spinner_thread is not None and self._spinner_thread.is_alive():
                return
            self._spinner_generation += 1
            generation = self._spinner_generation
            self._stop_event.clear()
            self._spinner_thread = threading.Thread(
                target=self._spin_loop,
                args=(generation,),
                name="afk-chat-spinner",
                daemon=True,
            )
            self._spinner_thread.start()

    def _spin_loop(self, generation: int) -> None:
        frame_index = 0
        while not self._stop_event.is_set():
            with self._lock:
                if generation != self._spinner_generation:
                    break
                label = self._active_label or "working..."
                color = self._active_color
            frame = _DOT_FRAMES[frame_index % len(_DOT_FRAMES)]
            rendered = f"\r  {color}{label}{frame}\033[0m"
            self._write_line(rendered)
            frame_index += 1
            time.sleep(0.08)

    @staticmethod
    def _print_status_line(line: str, color: str) -> None:
        sys.stdout.write(f"  {color}{line}\033[0m\n")
        sys.stdout.flush()

    @staticmethod
    def _print_detail_line(line: str) -> None:
        sys.stdout.write(f"    \033[90m{line}\033[0m\n")
        sys.stdout.flush()

    @staticmethod
    def _print_group_separator() -> None:
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _write_line(self, rendered: str) -> None:
        clean_len = len(_strip_ansi(rendered).lstrip("\r"))
        self._last_render_width = max(self._last_render_width, clean_len)
        sys.stdout.write(rendered)
        sys.stdout.flush()

    def _clear_line(self) -> None:
        if self._last_render_width <= 0:
            return
        sys.stdout.write("\r" + (" " * self._last_render_width) + "\r")
        sys.stdout.flush()
        self._last_render_width = 0


def _strip_ansi(value: str) -> str:
    out: list[str] = []
    in_escape = False
    for char in value:
        if char == "\x1b":
            in_escape = True
            continue
        if in_escape:
            if char == "m":
                in_escape = False
            continue
        out.append(char)
    return "".join(out)
