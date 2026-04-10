"""Queue state used by interactive chat REPL sessions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DEFAULT_MAX_CHAT_REPL_QUEUE_SIZE = 3


class ChatReplQueueFullError(RuntimeError):
    """Raised when the interactive follow-up queue is full."""


@dataclass(slots=True)
class ChatReplTurnQueue:
    """Track queued follow-up messages and pending exit requests."""

    _messages: deque[str] = field(default_factory=deque)
    max_size: int = DEFAULT_MAX_CHAT_REPL_QUEUE_SIZE
    exit_requested: bool = False

    def enqueue(self, message: str) -> int:
        """Append one message and return the resulting queue length."""

        if self.full:
            raise ChatReplQueueFullError("chat follow-up queue is full")
        self._messages.append(message)
        return len(self._messages)

    def pop_next(self) -> str | None:
        """Return the next queued message, if one exists."""

        if not self._messages:
            return None
        return self._messages.popleft()

    def request_exit(self) -> None:
        """Mark the session for exit and drop queued follow-up messages."""

        self.exit_requested = True
        self.clear()

    def clear(self) -> None:
        """Drop queued follow-up messages while keeping the session open."""

        self._messages.clear()

    @property
    def size(self) -> int:
        """Return the number of queued follow-up messages."""

        return len(self._messages)

    @property
    def full(self) -> bool:
        """Return whether the follow-up message queue is full."""

        return len(self._messages) >= max(0, self.max_size)
