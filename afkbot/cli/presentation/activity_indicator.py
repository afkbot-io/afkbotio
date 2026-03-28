"""Small reusable CLI activity indicator for long-running interactive commands."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TextIO

_DOT_FRAMES: tuple[str, ...] = (".  ", ".. ", "...")


@dataclass(slots=True)
class ActivityIndicator:
    """Render one lightweight spinner or fallback status line for CLI work."""

    label: str
    color: str = "\033[94m"
    enabled: bool = field(
        default_factory=lambda: bool(sys.stdin.isatty() and sys.stdout.isatty())
    )
    stream: TextIO = field(default_factory=lambda: sys.stdout)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _generation: int = field(default=0, init=False)
    _last_render_width: int = field(default=0, init=False)

    def __enter__(self) -> ActivityIndicator:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = exc_type, exc, tb
        self.stop()

    def start(self) -> None:
        """Start spinner or print one fallback progress line."""

        if self.enabled:
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return
                self._generation += 1
                generation = self._generation
                self._stop_event.clear()
                self._thread = threading.Thread(
                    target=self._spin_loop,
                    args=(generation,),
                    name="afk-cli-activity",
                    daemon=True,
                )
                self._thread.start()
            return
        self.stream.write(f"{self.label}...\n")
        self.stream.flush()

    def stop(self) -> None:
        """Stop spinner and clear transient progress line when necessary."""

        with self._lock:
            thread = self._thread
            self._thread = None
            self._generation += 1
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=0.5)
        if self.enabled:
            self._clear_line()

    def _spin_loop(self, generation: int) -> None:
        frame_index = 0
        while not self._stop_event.is_set():
            with self._lock:
                if generation != self._generation:
                    break
            frame = _DOT_FRAMES[frame_index % len(_DOT_FRAMES)]
            rendered = f"\r{self.color}{self.label}{frame}\033[0m"
            self._write_line(rendered)
            frame_index += 1
            time.sleep(0.12)

    def _write_line(self, rendered: str) -> None:
        clean_len = len(_strip_ansi(rendered).lstrip("\r"))
        self._last_render_width = max(self._last_render_width, clean_len)
        self.stream.write(rendered)
        self.stream.flush()

    def _clear_line(self) -> None:
        if self._last_render_width <= 0:
            return
        self.stream.write("\r" + (" " * self._last_render_width) + "\r")
        self.stream.flush()
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
