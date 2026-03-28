"""Deterministic extraction of durable semantic memory facts from finalized turns."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from afkbot.services.memory.contracts import MemoryKind

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|привет|здравствуй|добрый день|ок|okay|thanks|thank you|спасибо)\b",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(api[_ -]?key|token|password|парол|session[_ -]?string|otp|2fa|код подтверждения|code)",
    re.IGNORECASE,
)
_HEX_SECRET_RE = re.compile(r"\b[a-f0-9]{24,}\b", re.IGNORECASE)
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
_GLOBAL_RE = re.compile(
    r"(по умолчанию|во всех чатах|везде|глобально|для всего профиля|for all chats|globally|by default)",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"(?:[\n\r]+|[.!?;]+)\s*")


@dataclass(frozen=True, slots=True)
class ExtractedMemoryRecord:
    """One deterministic semantic memory record extracted from one finalized turn."""

    memory_key: str
    summary: str
    details_md: str
    memory_kind: MemoryKind
    promote_global: bool = False


def extract_memory_records(
    *,
    user_message: str,
    assistant_message: str,
    max_chars: int,
    allowed_kinds: tuple[str, ...],
) -> tuple[ExtractedMemoryRecord, ...]:
    """Extract durable memory candidates from one finalized turn."""

    normalized_user = _normalize_text(user_message)
    normalized_assistant = _normalize_text(assistant_message)
    if _should_skip(normalized_user):
        return ()
    allowed_kind_set = set(allowed_kinds)
    records: list[ExtractedMemoryRecord] = []
    seen_keys: set[str] = set()
    for candidate in _split_candidates(normalized_user):
        if _should_skip(candidate):
            continue
        memory_kind = _classify_memory_kind(candidate)
        if memory_kind not in allowed_kind_set or memory_kind == "note":
            continue
        summary = _build_summary(text=candidate, memory_kind=memory_kind, max_chars=max_chars)
        memory_key = _build_memory_key(summary=summary, memory_kind=memory_kind)
        if memory_key in seen_keys:
            continue
        seen_keys.add(memory_key)
        records.append(
            ExtractedMemoryRecord(
                memory_key=memory_key,
                summary=summary,
                details_md=_build_details(
                    user_message=candidate,
                    assistant_message=normalized_assistant,
                    max_chars=max_chars,
                ),
                memory_kind=memory_kind,
                promote_global=_GLOBAL_RE.search(candidate) is not None,
            )
        )
        if len(records) >= 3:
            break
    return tuple(records)


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
    if _SECRET_RE.search(user_message) or _HEX_SECRET_RE.search(user_message):
        return True
    return False


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


def _build_memory_key(*, summary: str, memory_kind: MemoryKind) -> str:
    digest = hashlib.sha1(summary.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    return f"auto-{memory_kind}-{digest}"
