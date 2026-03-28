"""Tests for REPL queue state used by interactive chat sessions."""

from __future__ import annotations

from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue


def test_chat_repl_turn_queue_tracks_messages_and_exit_requests() -> None:
    """The REPL queue should preserve FIFO order and clear pending items on exit."""

    # Arrange
    queue = ChatReplTurnQueue()

    # Act
    first_size = queue.enqueue("first")
    second_size = queue.enqueue("second")
    first_message = queue.pop_next()
    queue.request_exit()

    # Assert
    assert first_size == 1
    assert second_size == 2
    assert first_message == "first"
    assert queue.exit_requested is True
    assert queue.size == 0
