"""Shared hybrid summary helpers for session and request compaction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from afkbot.services.llm.contracts import LLMMessage, LLMProvider, LLMRequest

_SUMMARY_REQUEST_TIMEOUT_SEC = 45.0
_SOURCE_MAX_CHARS_FLOOR = 4_000
_SOURCE_MAX_CHARS_CEILING = 16_000
_SOURCE_MAX_CHARS_MULTIPLIER = 6
_SOURCE_CLIP_NOTICE = "[Compaction source clipped for budget]"
_SUMMARY_OUTPUT_NOTICE = "[Summary clipped for budget]"


@dataclass(frozen=True, slots=True)
class CompactionSummaryResult:
    """One bounded summary payload plus the strategy that produced it."""

    summary_text: str
    strategy: str


class CompactionSummaryRuntime:
    """Build bounded summaries with LLM-first hybrid fallback."""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider | None,
        max_chars: int,
    ) -> None:
        self._llm_provider = llm_provider
        self._max_chars = max(256, int(max_chars))

    async def summarize(
        self,
        *,
        instructions: str,
        source_sections: Sequence[tuple[str, str]],
        fallback_text: str,
        preserve_if_fits: bool = True,
    ) -> CompactionSummaryResult:
        """Return hybrid summary text bounded to the configured char budget."""

        normalized_fallback = self._normalize_output(fallback_text)
        if preserve_if_fits and len(normalized_fallback) <= self._max_chars:
            return CompactionSummaryResult(
                summary_text=normalized_fallback,
                strategy="deterministic_v1",
            )
        if self._llm_provider is None:
            return CompactionSummaryResult(
                summary_text=normalized_fallback,
                strategy="deterministic_v1",
            )

        source_text = self._render_source_sections(source_sections)
        if not source_text:
            return CompactionSummaryResult(
                summary_text=normalized_fallback,
                strategy="deterministic_v1",
            )

        request = LLMRequest(
            profile_id="system",
            session_id="system-compaction",
            context=instructions.strip(),
            history=[
                LLMMessage(
                    role="user",
                    content=self._clip_source_text(source_text),
                )
            ],
            available_tools=(),
            request_timeout_sec=_SUMMARY_REQUEST_TIMEOUT_SEC,
        )
        response = await self._llm_provider.complete(request)
        if response.kind != "final" or response.error_code:
            return CompactionSummaryResult(
                summary_text=normalized_fallback,
                strategy="deterministic_v1",
            )

        summary_text = self._normalize_output(response.final_message or "")
        if not summary_text:
            return CompactionSummaryResult(
                summary_text=normalized_fallback,
                strategy="deterministic_v1",
            )
        return CompactionSummaryResult(
            summary_text=summary_text,
            strategy="hybrid_llm_v1",
        )

    def _normalize_output(self, text: str) -> str:
        normalized = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
        if len(normalized) <= self._max_chars:
            return normalized
        suffix = f"\n{_SUMMARY_OUTPUT_NOTICE}"
        budget = max(0, self._max_chars - len(suffix))
        clipped = normalized[:budget].rstrip()
        if not clipped:
            return _SUMMARY_OUTPUT_NOTICE[: self._max_chars]
        return f"{clipped}{suffix}"

    @staticmethod
    def _render_source_sections(source_sections: Sequence[tuple[str, str]]) -> str:
        rendered: list[str] = []
        for title, body in source_sections:
            normalized_title = title.strip()
            normalized_body = body.strip()
            if not normalized_title or not normalized_body:
                continue
            rendered.append(f"## {normalized_title}\n{normalized_body}")
        return "\n\n".join(rendered).strip()

    def _clip_source_text(self, text: str) -> str:
        if len(text) <= self._source_budget_chars:
            return text
        notice = f"\n\n{_SOURCE_CLIP_NOTICE}\n\n"
        if self._source_budget_chars <= len(notice) + 32:
            return text[: self._source_budget_chars]
        head_budget = (self._source_budget_chars - len(notice)) // 2
        tail_budget = self._source_budget_chars - len(notice) - head_budget
        head = text[:head_budget].rstrip()
        tail = text[-tail_budget:].lstrip()
        return f"{head}{notice}{tail}".strip()

    @property
    def _source_budget_chars(self) -> int:
        return min(
            _SOURCE_MAX_CHARS_CEILING,
            max(_SOURCE_MAX_CHARS_FLOOR, self._max_chars * _SOURCE_MAX_CHARS_MULTIPLIER),
        )
