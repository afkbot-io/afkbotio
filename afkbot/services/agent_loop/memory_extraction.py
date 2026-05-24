"""Deterministic extraction of durable memory candidates from finalized turns."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.memory.contracts import MemoryKind

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|привет|здравствуй|добрый день|ок|okay|thanks|thank you|спасибо)\b",
    re.IGNORECASE,
)
_POLITE_PREFIX_RE = re.compile(
    r"^(?:thanks|thank you|спасибо|ок|okay|понял|принял|хорошо|ладно)[,!.:\s-]+",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(api[_ -]?key|token|password|парол|session[_ -]?string|otp|2fa|код подтверждения|code)",
    re.IGNORECASE,
)
_HEX_SECRET_RE = re.compile(r"\b[a-f0-9]{24,}\b", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:sk|ghp|xoxb|xoxp)-[A-Za-z0-9_-]{8,}\b|\bAKIA[0-9A-Z]{12,}\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(
    r"(prefer|preferred|предпочита|отвечай|пиши|говори|на русском|на английском|short answers|коротк|подробн)",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(r"(agreed|decided|решили|договорились|согласовали)", re.IGNORECASE)
_TASK_RE = re.compile(
    r"(deadline|due|todo|task|задач|нужно|надо|сделать|подготовить|до \d{1,2}[./]\d{1,2})",
    re.IGNORECASE,
)
_RISK_RE = re.compile(r"(risk|critical|urgent|риск|критич|срочно|проблем)", re.IGNORECASE)
_FACT_RE = re.compile(
    r"(my name is|меня зовут|я |мой |для этого клиента|в этом чате|this client|nickname is)",
    re.IGNORECASE,
)
_TEMPORARY_RE = re.compile(
    r"(на сегодня|временно|пока что|for today|temporar|for now|на время задачи|until tomorrow|до завтра)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"(?:[\n\r]+|[.!?;]+)\s*")
_CLAUSE_SPLIT_RE = re.compile(r"(?:,\s*(?:а|но)?\s*|:\s*|\s+(?:а|но|but|however)\s+)")


@dataclass(frozen=True, slots=True)
class ExtractedMemoryCandidate:
    """One extracted durable memory candidate before storage-policy decisions."""

    source_text: str
    summary: str
    details_md: str
    memory_kind: MemoryKind


def extract_memory_candidates(
    *,
    user_message: str,
    assistant_message: str,
    max_chars: int,
    allowed_kinds: tuple[str, ...],
) -> tuple[ExtractedMemoryCandidate, ...]:
    """Extract durable memory candidates from one finalized turn."""

    normalized_user = _normalize_text(user_message)
    normalized_assistant = _normalize_text(assistant_message)
    if not normalized_user:
        return ()
    allowed_kind_set = set(allowed_kinds)
    candidates: list[ExtractedMemoryCandidate] = []
    seen_summaries: set[str] = set()
    for candidate_text in _split_candidates(normalized_user):
        for raw_fragment in _expand_candidate_fragments(candidate_text):
            cleaned_candidate = _strip_polite_prefix(raw_fragment)
            if _should_skip(cleaned_candidate):
                continue
            memory_kind = _classify_memory_kind(cleaned_candidate)
            if memory_kind not in allowed_kind_set or memory_kind == "note":
                continue
            summary = _build_summary(
                text=cleaned_candidate, memory_kind=memory_kind, max_chars=max_chars
            )
            if summary in seen_summaries:
                continue
            seen_summaries.add(summary)
            candidates.append(
                ExtractedMemoryCandidate(
                    source_text=cleaned_candidate,
                    summary=summary,
                    details_md=_build_details(
                        user_message=cleaned_candidate,
                        assistant_message=normalized_assistant,
                        max_chars=max_chars,
                    ),
                    memory_kind=memory_kind,
                )
            )
            if len(candidates) >= 3:
                return tuple(candidates)
    return tuple(candidates)


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _split_candidates(user_message: str) -> tuple[str, ...]:
    return tuple(
        candidate
        for candidate in (_normalize_text(item) for item in _SPLIT_RE.split(user_message))
        if candidate
    )


def _should_skip(user_message: str) -> bool:
    if len(user_message) < 12:
        return True
    if _GREETING_RE.search(user_message):
        return True
    if _TEMPORARY_RE.search(user_message):
        return True
    if (
        _SECRET_RE.search(user_message)
        or _HEX_SECRET_RE.search(user_message)
        or _SECRET_VALUE_RE.search(user_message)
    ):
        return True
    return False


def _strip_polite_prefix(user_message: str) -> str:
    return _POLITE_PREFIX_RE.sub("", user_message, count=1).strip()


def _expand_candidate_fragments(user_message: str) -> tuple[str, ...]:
    normalized = user_message.strip()
    if not normalized:
        return ()
    if _TEMPORARY_RE.search(normalized) is None:
        return (normalized,)
    fragments = tuple(
        fragment.strip() for fragment in _CLAUSE_SPLIT_RE.split(normalized) if fragment.strip()
    )
    return fragments or (normalized,)


def _classify_memory_kind(user_message: str) -> MemoryKind:
    if _PREFERENCE_RE.search(user_message):
        return "preference"
    if _DECISION_RE.search(user_message):
        return "decision"
    if _TASK_RE.search(user_message):
        return "task"
    if _RISK_RE.search(user_message):
        return "risk"
    if _FACT_RE.search(user_message):
        return "fact"
    return "note"


def _build_summary(*, text: str, memory_kind: MemoryKind, max_chars: int) -> str:
    prefix = {
        "fact": "Chat fact",
        "preference": "Chat preference",
        "decision": "Chat decision",
        "task": "Chat task",
        "risk": "Chat risk",
        "note": "Chat note",
    }[memory_kind]
    summary = f"{prefix}: {text}"
    if len(summary) <= max_chars:
        return summary
    return summary[: max(16, max_chars - 3)].rstrip() + "..."


def _build_details(*, user_message: str, assistant_message: str, max_chars: int) -> str:
    details = f"User said: {user_message}\nAssistant concluded: {assistant_message}"
    if len(details) <= max_chars:
        return details
    return details[: max(16, max_chars - 3)].rstrip() + "..."
