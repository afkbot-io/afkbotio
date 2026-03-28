"""Input-consumption helpers for interactive chat REPL sessions."""

from __future__ import annotations

from afkbot.cli.commands.chat_repl_controls import handle_chat_repl_local_command
from afkbot.services.chat_session.repl_input import ChatReplInputOutcome
from afkbot.services.chat_session.repl_queue import ChatReplTurnQueue
from afkbot.services.chat_session.session_state import ChatReplSessionState

_EXIT_NOTICE = "Exit requested after current turn. Pending queue cleared."


def consume_chat_repl_input(
    *,
    raw_message: str,
    repl_state: ChatReplSessionState,
    turn_queue: ChatReplTurnQueue,
    turn_active: bool,
    queue_messages: bool = True,
) -> ChatReplInputOutcome:
    """Apply one input line to local controls, exit flow, or the queued turn FIFO."""

    local_command = handle_chat_repl_local_command(raw_message, state=repl_state)
    if local_command.consumed:
        if local_command.exit_repl:
            turn_queue.request_exit()
            repl_state.queued_messages = 0
            if turn_active:
                return ChatReplInputOutcome(
                    consumed=True,
                    exit_repl=False,
                    message=local_command.message,
                    notice=_EXIT_NOTICE,
                )
            return ChatReplInputOutcome(
                consumed=True,
                exit_repl=True,
                message=local_command.message,
            )
        return ChatReplInputOutcome(consumed=True, message=local_command.message)

    normalized = raw_message.strip().lower()
    if normalized in {"exit", "quit"}:
        turn_queue.request_exit()
        repl_state.queued_messages = 0
        if turn_active:
            return ChatReplInputOutcome(
                consumed=True,
                exit_repl=False,
                notice=_EXIT_NOTICE,
            )
        return ChatReplInputOutcome(consumed=True, exit_repl=True)
    if not raw_message.strip():
        return ChatReplInputOutcome(consumed=True)
    if not queue_messages:
        return ChatReplInputOutcome(consumed=False)

    pending_count = turn_queue.enqueue(raw_message)
    repl_state.queued_messages = pending_count
    return ChatReplInputOutcome(
        consumed=True,
        queued_message=raw_message,
        notice=(
            f"Queued next message. Pending queue: {pending_count}"
            if turn_active
            else None
        ),
    )
