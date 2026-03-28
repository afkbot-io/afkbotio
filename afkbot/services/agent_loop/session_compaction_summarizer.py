"""Deterministic compaction summary builder for older chat turns."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.models.chat_turn import ChatTurn

_TURN_SEPARATOR = "\n"
_TRUNCATION_NOTICE = "[Earlier compacted turns pruned for brevity]"


class SessionCompactionSummarizer:
    """Build bounded trusted summaries without another model call."""

    _USER_CHARS = 180
    _ASSISTANT_CHARS = 240

    def __init__(self, *, max_chars: int) -> None:
        self._max_chars = max(256, max_chars)

    def extend(
        self,
        *,
        existing_summary: str | None,
        new_turns: Sequence[ChatTurn],
    ) -> str:
        """Append new compacted turns to existing summary and prune to budget."""

        blocks: list[str] = []
        normalized_existing = (existing_summary or "").strip()
        if normalized_existing:
            blocks.append(normalized_existing)
        blocks.extend(self._render_turn_block(turn) for turn in new_turns)
        if not blocks:
            return ""
        return self._prune_blocks(blocks)

    def _prune_blocks(self, blocks: Sequence[str]) -> str:
        text = _TURN_SEPARATOR.join(blocks)
        if len(text) <= self._max_chars:
            return text
        kept: list[str] = []
        size = len(_TRUNCATION_NOTICE)
        for block in reversed(blocks):
            candidate_size = size + len(_TURN_SEPARATOR) + len(block)
            if kept and candidate_size > self._max_chars:
                break
            kept.append(block)
            size = candidate_size
        kept.reverse()
        compacted = _TURN_SEPARATOR.join([_TRUNCATION_NOTICE, *kept])
        return compacted[-self._max_chars :].lstrip()

    def _render_turn_block(self, turn: ChatTurn) -> str:
        user = self._normalize_excerpt(turn.user_message, limit=self._USER_CHARS)
        assistant = self._normalize_excerpt(turn.assistant_message, limit=self._ASSISTANT_CHARS)
        return f"- [T{turn.id}] User: {user}\n  Assistant: {assistant}"

    @staticmethod
    def _normalize_excerpt(text: str, *, limit: int) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[: max(0, limit - 3)].rstrip()}..."
