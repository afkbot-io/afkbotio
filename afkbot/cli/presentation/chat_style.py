"""Shared chat presentation constants."""

from __future__ import annotations

from prompt_toolkit.formatted_text import HTML

AFK_AGENT_HEADER = "\033[96mAFK Agent\033[0m"
CHAT_PROMPT_MARKUP = "<ansibright_green>you</ansibright_green><ansigray> > </ansigray>"
CHAT_PROMPT = HTML(CHAT_PROMPT_MARKUP)
