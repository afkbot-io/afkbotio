"""Queue state used by interactive chat REPL sessions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class ChatReplTurnQueue:
    """Track queued follow-up messages and pending exit requests."""

    _messages: deque[str] = field(default_factory=deque)
    exit_requested: bool = False

    def enqueue(self, message: str) -> int:
        """Append one message and return the resulting queue length."""

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
        self._messages.clear()

    @property
    def size(self) -> int:
        """Return the number of queued follow-up messages."""

        return len(self._messages)
