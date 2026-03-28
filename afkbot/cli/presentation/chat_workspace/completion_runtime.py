"""Completion helpers for the fullscreen chat workspace composer."""

from __future__ import annotations

import asyncio
from typing import cast

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document


def current_completion(completion_state: object | None) -> Completion | None:
    """Return the currently selected completion from prompt-toolkit state."""

    if completion_state is None:
        return None
    selected = getattr(completion_state, "current_completion", None)
    if selected is not None:
        return cast(Completion, selected)
    completions = getattr(completion_state, "completions", ())
    if not completions:
        return None
    return cast(Completion, completions[0])


def should_auto_submit_inline_command(*, draft: str, completion: Completion) -> bool:
    """Return whether one selected slash command should submit immediately."""

    stripped = draft.strip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return False
    if " " in stripped:
        return False
    completion_text = str(completion.text).strip()
    return completion_text.startswith("/") and not completion_text.startswith("//")


def apply_completion(buffer: Buffer, completion: Completion) -> None:
    """Apply one completion, even when no prompt-toolkit event loop is running."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _apply_completion_without_running_loop(buffer, completion)
        return
    buffer.apply_completion(completion)


def _apply_completion_without_running_loop(buffer: Buffer, completion: Completion) -> None:
    """Apply one completion without triggering prompt-toolkit background tasks."""

    document = buffer.document
    start_index = max(0, document.cursor_position + completion.start_position)
    replacement_end = document.cursor_position
    next_text = document.text[:start_index] + completion.text + document.text[replacement_end:]
    next_cursor = start_index + len(completion.text)
    buffer.set_document(
        Document(text=next_text, cursor_position=next_cursor),
        bypass_readonly=True,
    )
    buffer.complete_state = None
