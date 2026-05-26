"""Periodic transcript progress heartbeat for non-fullscreen CLI output."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from afkbot.cli.presentation.elapsed import format_elapsed_seconds


ProgressHeartbeatEmitter = Callable[[str, str], None]


@dataclass(slots=True)
class TranscriptProgressHeartbeat:
    """Emit periodic `Working (...)` lines while a CLI turn remains in flight."""

    emit: ProgressHeartbeatEmitter
    interval_sec: float = 5.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _generation: int = 0
    _started_at: float | None = None
    _active_label: str | None = None
    _active_color: str = "\033[94m"

    def begin_turn(self) -> None:
        """Reset elapsed timing for the next assistant turn."""

        self.stop()
        with self._lock:
            self._started_at = time.monotonic()

    def update(self, label: str, color: str) -> None:
        """Record the latest visible activity and ensure heartbeat is running."""

        if self.interval_sec <= 0:
            return
        with self._lock:
            if self._started_at is None:
                self._started_at = time.monotonic()
            self._active_label = label
            self._active_color = color
            thread = self._thread
            if thread is not None and thread.is_alive():
                return
            self._generation += 1
            generation = self._generation
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                args=(generation,),
                name="afk-chat-transcript-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop heartbeat output and clear the active turn state."""

        with self._lock:
            thread = self._thread
            self._thread = None
            self._generation += 1
            self._started_at = None
            self._active_label = None
            self._active_color = "\033[94m"
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=0.5)

    def _loop(self, generation: int) -> None:
        while not self._stop_event.wait(self.interval_sec):
            with self._lock:
                if generation != self._generation:
                    break
                label = self._active_label
                color = self._active_color
                started_at = self._started_at
            if label is None or started_at is None:
                continue
            elapsed = format_elapsed_seconds(time.monotonic() - started_at)
            self.emit(f"Working ({elapsed}) · {label}", color)
