"""Hybrid overflow-recovery compaction for LLM requests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from afkbot.services.agent_loop.compaction_summary import CompactionSummaryRuntime
from afkbot.services.llm.contracts import LLMMessage, LLMProvider, LLMRequest, ToolCallRequest

_OLDER_HISTORY_SUMMARY_INSTRUCTIONS = """
You are compacting earlier chat history for an AI agent after a context-window overflow.

Write a concise English handoff summary.
Preserve the user goal, important constraints, completed work, key tool findings, and the next unresolved step.
Do not mention token limits, compaction, or implementation details about this summarization step.
Use short plain-text bullets or short mini-sections.
""".strip()

_OLDER_HISTORY_NOTICE = (
    "Trusted compact handoff summary for pruned earlier context. "
    "Use it instead of the removed raw history.\n"
)
_HISTORY_TRUNCATION_NOTICE = "[Earlier history clipped for automatic context compaction]"
_CONTEXT_TRUNCATION_NOTICE = "[Section truncated for automatic context compaction]"
_CONTEXT_PRIORITY: dict[str, int] = {
    "# Bootstrap": 0,
    "# Binding Prompt Overlay": 0,
    "# Runtime Safety Policy": 0,
    "# Selected Skill Cards": 1,
    "# Explicit Skill Instructions": 1,
    "# Explicit Subagent Instructions": 1,
    "# Trusted Runtime Notes": 1,
    "# Runtime Metadata (untrusted)": 2,
    "# Skills": 3,
    "# Subagents": 3,
}


@dataclass(frozen=True, slots=True)
class LLMRequestCompactionResult:
    """One compacted request plus visible telemetry fields."""

    request: LLMRequest
    summary_strategy: str
    summary_chars: int
    preserved_recent_messages: int
    history_messages_before: int
    history_messages_after: int
    context_chars_before: int
    context_chars_after: int
    compacted_history: bool
    compacted_context: bool


class LLMRequestCompactionService:
    """Compact oversized provider requests while preserving core instructions."""

    _MESSAGE_CONTENT_CHARS = 320
    _TOOL_ARGUMENT_CHARS = 240
    _TOOL_RESULT_CHARS = 360

    def __init__(
        self,
        *,
        llm_provider: LLMProvider | None,
        max_summary_chars: int,
        keep_recent_turns: int,
    ) -> None:
        self._max_summary_chars = max(256, int(max_summary_chars))
        self._keep_recent_turns = max(1, int(keep_recent_turns))
        self._summary_runtime = CompactionSummaryRuntime(
            llm_provider=llm_provider,
            max_chars=self._max_summary_chars,
        )

    async def compact_for_overflow(
        self,
        *,
        request: LLMRequest,
        attempt: int,
    ) -> LLMRequestCompactionResult | None:
        """Return a more compact request for one overflow retry attempt."""

        if attempt < 1:
            return None

        history_before = list(request.history)
        history_after = list(history_before)
        context_before = request.context
        context_after = context_before
        summary_text = ""
        summary_strategy = "deterministic_v1"
        compacted_history = False
        compacted_context = False

        keep_recent_messages = min(
            len(history_before),
            self._resolve_keep_recent_messages(attempt=attempt),
        )
        older_history = history_before[:-keep_recent_messages] if keep_recent_messages > 0 else history_before
        recent_history = history_before[-keep_recent_messages:] if keep_recent_messages > 0 else []

        if older_history:
            summary_result = await self._summary_runtime.summarize(
                instructions=_OLDER_HISTORY_SUMMARY_INSTRUCTIONS,
                source_sections=[("Earlier history", self._render_history_source(older_history))],
                fallback_text=self._render_deterministic_history_summary(older_history),
                preserve_if_fits=False,
            )
            summary_text = summary_result.summary_text.strip()
            summary_strategy = summary_result.strategy
            history_after = []
            if summary_text:
                history_after.append(
                    LLMMessage(
                        role="system",
                        content=f"{_OLDER_HISTORY_NOTICE}{summary_text}",
                    )
                )
            history_after.extend(recent_history)
            compacted_history = self._history_changed(
                before=history_before,
                after=history_after,
            )

        if attempt >= 2 or not compacted_history:
            compacted_context_text = self._compact_context_text(
                context_before,
                attempt=attempt,
            )
            if compacted_context_text != context_before:
                context_after = compacted_context_text
                compacted_context = True

        if not compacted_history and not compacted_context:
            return None

        compacted_request = request.model_copy(
            update={
                "context": context_after,
                "history": history_after,
            }
        )
        return LLMRequestCompactionResult(
            request=compacted_request,
            summary_strategy=summary_strategy,
            summary_chars=len(summary_text),
            preserved_recent_messages=keep_recent_messages,
            history_messages_before=len(history_before),
            history_messages_after=len(history_after),
            context_chars_before=len(context_before),
            context_chars_after=len(context_after),
            compacted_history=compacted_history,
            compacted_context=compacted_context,
        )

    def _resolve_keep_recent_messages(self, *, attempt: int) -> int:
        base_keep = max(3, self._keep_recent_turns * 2 + 1)
        if attempt <= 1:
            return base_keep
        return max(2, base_keep // attempt)

    def _render_history_source(self, history: Sequence[LLMMessage]) -> str:
        blocks = [self._render_history_message(message) for message in history]
        return "\n".join(block for block in blocks if block)

    def _render_deterministic_history_summary(self, history: Sequence[LLMMessage]) -> str:
        blocks = [self._render_history_message(message) for message in history]
        normalized_blocks = [block for block in blocks if block]
        if not normalized_blocks:
            return ""
        text = "\n".join(normalized_blocks)
        if len(text) <= self._max_summary_chars:
            return text
        notice = f"{_HISTORY_TRUNCATION_NOTICE}\n"
        budget = max(0, self._max_summary_chars - len(notice))
        clipped = text[-budget:].lstrip()
        return f"{notice}{clipped}".strip()

    def _render_history_message(self, message: LLMMessage) -> str:
        role = message.role
        if role == "assistant" and message.tool_calls:
            rendered_calls = ", ".join(self._render_tool_call(call) for call in message.tool_calls[:4])
            return f"- Assistant requested tools: {rendered_calls}"
        if role == "tool":
            tool_name = (message.tool_name or "tool").strip()
            content = self._truncate_text(message.content or "", limit=self._TOOL_RESULT_CHARS)
            return f"- Tool {tool_name} result: {content}"
        content = self._truncate_text(message.content or "", limit=self._MESSAGE_CONTENT_CHARS)
        if not content:
            return ""
        return f"- {role.title()}: {content}"

    def _render_tool_call(self, call: ToolCallRequest) -> str:
        rendered = call.name.strip()
        if call.params:
            arguments = self._truncate_text(str(call.params), limit=self._TOOL_ARGUMENT_CHARS)
            rendered = f"{rendered} {arguments}"
        return rendered.strip()

    @staticmethod
    def _truncate_text(text: str, *, limit: int) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: max(0, limit - 3)].rstrip()}..."

    @staticmethod
    def _history_changed(*, before: Sequence[LLMMessage], after: Sequence[LLMMessage]) -> bool:
        if len(before) != len(after):
            return True
        for before_item, after_item in zip(before, after, strict=True):
            if before_item != after_item:
                return True
        return False

    def _compact_context_text(self, context: str, *, attempt: int) -> str:
        max_chars = self._context_budget_for_attempt(attempt=attempt)
        if len(context) <= max_chars:
            return context

        sections = self._split_context_sections(context)
        if not sections:
            return self._clip_text(context, max_chars=max_chars)

        rendered_sections: list[tuple[int, str]] = []
        for heading, body in sections:
            priority = _CONTEXT_PRIORITY.get(heading, 2)
            excerpt_limit = self._context_excerpt_limit(priority=priority, attempt=attempt)
            normalized_body = body.strip()
            if len(normalized_body) > excerpt_limit:
                normalized_body = f"{normalized_body[:excerpt_limit].rstrip()}\n{_CONTEXT_TRUNCATION_NOTICE}"
            rendered_sections.append((priority, f"{heading}\n{normalized_body}".strip()))
        return self._fit_context_sections(rendered_sections, max_chars=max_chars)

    def _context_budget_for_attempt(self, *, attempt: int) -> int:
        base_budget = max(self._max_summary_chars * 3, 2_400)
        if attempt <= 1:
            return base_budget
        return max(1_600, base_budget // attempt)

    @staticmethod
    def _context_excerpt_limit(*, priority: int, attempt: int) -> int:
        if priority == 0:
            return 520 if attempt <= 2 else 360
        if priority == 1:
            return 320 if attempt <= 2 else 220
        if priority == 2:
            return 180 if attempt <= 2 else 140
        return 100 if attempt <= 2 else 80

    @staticmethod
    def _split_context_sections(context: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_heading: str | None = None
        current_lines: list[str] = []
        for line in context.splitlines():
            if line.startswith("# "):
                if current_heading is not None:
                    sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = line.strip()
                current_lines = []
                continue
            current_lines.append(line)
        if current_heading is not None:
            sections.append((current_heading, "\n".join(current_lines).strip()))
        return sections

    @staticmethod
    def _clip_text(text: str, *, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        notice = f"\n\n{_CONTEXT_TRUNCATION_NOTICE}\n\n"
        if max_chars <= len(notice) + 32:
            return text[:max_chars]
        head_budget = (max_chars - len(notice)) // 2
        tail_budget = max_chars - len(notice) - head_budget
        head = text[:head_budget].rstrip()
        tail = text[-tail_budget:].lstrip()
        return f"{head}{notice}{tail}".strip()

    def _fit_context_sections(
        self,
        rendered_sections: Sequence[tuple[int, str]],
        *,
        max_chars: int,
    ) -> str:
        ordered_sections = [section for _, section in rendered_sections if section]
        compacted = "\n\n".join(ordered_sections).strip()
        if len(compacted) <= max_chars:
            return compacted

        selected_indexes: list[int] = []
        used_chars = 0
        for index, (priority, section) in sorted(
            enumerate(rendered_sections),
            key=lambda item: (item[1][0], item[0]),
        ):
            _ = priority
            extra_chars = len(section) + (2 if selected_indexes else 0)
            if used_chars + extra_chars > max_chars and selected_indexes:
                continue
            if used_chars + extra_chars > max_chars:
                section = self._clip_text(section, max_chars=max_chars)
                selected_indexes = [index]
                used_chars = len(section)
                rendered_sections = list(rendered_sections)
                rendered_sections[index] = (rendered_sections[index][0], section)
                break
            selected_indexes.append(index)
            used_chars += extra_chars

        if not selected_indexes:
            return self._clip_text(compacted, max_chars=max_chars)
        selected_indexes.sort()
        fitted = "\n\n".join(rendered_sections[index][1] for index in selected_indexes).strip()
        if len(fitted) <= max_chars:
            return fitted
        return self._clip_text(fitted, max_chars=max_chars)
