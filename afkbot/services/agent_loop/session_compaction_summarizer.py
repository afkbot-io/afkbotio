"""Hybrid compaction summary builder for persisted older chat turns."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from afkbot.models.chat_turn import ChatTurn
from afkbot.services.agent_loop.compaction_summary import (
    CompactionSummaryRuntime,
)
from afkbot.services.llm.contracts import LLMProvider

_TURN_SEPARATOR = "\n"
_TRUNCATION_NOTICE = "[Earlier compacted turns pruned for brevity]"
_SESSION_COMPACTION_INSTRUCTIONS = """
You are compressing older persisted chat turns for an AI agent runtime.

Write a compact handoff summary in English.
Preserve durable constraints, key facts, completed work, tool findings, and pending next steps.
Do not add speculation.
Do not mention token limits, compaction internals, or that the model made the summary.
Use short plain-text bullets or mini-sections.
Stay concise and bounded.
""".strip()


@dataclass(frozen=True, slots=True)
class SessionCompactionBuild:
    """Bounded summary plus the strategy that produced it."""

    summary_text: str
    strategy: str


class SessionCompactionSummarizer:
    """Build bounded trusted summaries without losing recent turn coverage."""

    _USER_CHARS = 180
    _ASSISTANT_CHARS = 240

    def __init__(
        self,
        *,
        max_chars: int,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._max_chars = max(256, int(max_chars))
        self._summary_runtime = CompactionSummaryRuntime(
            llm_provider=llm_provider,
            max_chars=self._max_chars,
        )

    async def extend(
        self,
        *,
        existing_summary: str | None,
        new_turns: Sequence[ChatTurn],
    ) -> SessionCompactionBuild:
        """Append new compacted turns to summary and rewrite it when budget is tight."""

        blocks: list[str] = []
        normalized_existing = (existing_summary or "").strip()
        if normalized_existing:
            blocks.append(normalized_existing)
        new_turn_blocks = [self._render_turn_block(turn) for turn in new_turns]
        blocks.extend(new_turn_blocks)
        if not blocks:
            return SessionCompactionBuild(summary_text="", strategy="deterministic_v1")

        raw_text = _TURN_SEPARATOR.join(blocks)
        deterministic_text = self._prune_blocks(blocks)
        if len(raw_text) <= self._max_chars:
            return SessionCompactionBuild(
                summary_text=deterministic_text,
                strategy="deterministic_v1",
            )

        source_sections: list[tuple[str, str]] = []
        if normalized_existing:
            source_sections.append(("Existing trusted summary", normalized_existing))
        if new_turn_blocks:
            source_sections.append(("New turns to merge", _TURN_SEPARATOR.join(new_turn_blocks)))
        result = await self._summary_runtime.summarize(
            instructions=_SESSION_COMPACTION_INSTRUCTIONS,
            source_sections=source_sections,
            fallback_text=deterministic_text,
            preserve_if_fits=False,
        )
        return SessionCompactionBuild(
            summary_text=result.summary_text,
            strategy=result.strategy,
        )

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
