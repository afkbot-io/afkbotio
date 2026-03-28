"""Asynchronous prompt capture helpers for interactive chat sessions."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import cast

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import AnyFormattedText
from prompt_toolkit.patch_stdout import patch_stdout

from afkbot.cli.presentation.chat_style import CHAT_PROMPT


class ChatInputReader:
    """Read chat input with prompt-toolkit while other terminal output continues."""

    def __init__(
        self,
        *,
        prompt_session: PromptSession[str] | None,
        on_prompt_activity: Callable[[bool], None] | None = None,
        prompt_message: AnyFormattedText | None = None,
    ) -> None:
        self._prompt_session = prompt_session
        self._on_prompt_activity = on_prompt_activity
        self._prompt_message = CHAT_PROMPT if prompt_message is None else prompt_message
        self._prompt_count = 0

    async def read_input(self) -> str:
        """Read one user message, using async prompt-toolkit when available."""

        self._prompt_count += 1
        if self._prompt_count > 1:
            sys.stdout.write("\n")
            sys.stdout.flush()

        if self._prompt_session is None:
            return await asyncio.to_thread(input, "you > ")

        prompt_async = getattr(self._prompt_session, "prompt_async", None)
        if not callable(prompt_async):
            return await asyncio.to_thread(
                self._prompt_session.prompt,
                self._prompt_message,
            )

        if self._on_prompt_activity is not None:
            self._on_prompt_activity(True)
        try:
            with patch_stdout():
                return cast(str, await prompt_async(self._prompt_message))
        finally:
            if self._on_prompt_activity is not None:
                self._on_prompt_activity(False)
