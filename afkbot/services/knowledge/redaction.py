"""Fail-closed redaction checks for derived project knowledge."""

from __future__ import annotations

from dataclasses import dataclass
import re

_SECRET_PATTERN = re.compile(
    r"("
    r"api[_ -]?key|"
    r"access[_ -]?token|"
    r"refresh[_ -]?token|"
    r"bearer\s+[a-z0-9._-]{12,}|"
    r"password|"
    r"secret|"
    r"sk-[a-z0-9_-]{8,}|"
    r"gh[pousr]_[a-z0-9_]{20,}|"
    r"xox[baprs]-[a-z0-9-]{20,}"
    r")",
    re.IGNORECASE,
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)?PRIVATE KEY-----",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class KnowledgeRedactionResult:
    """Result of a knowledge capture safety check."""

    allowed: bool
    text: str
    reason_code: str | None = None


def screen_knowledge_text(text: str, *, allow_email: bool = False) -> KnowledgeRedactionResult:
    """Return whether text is safe to persist in a derived knowledge artifact."""

    normalized = str(text or "").strip()
    if not normalized:
        return KnowledgeRedactionResult(allowed=False, text="", reason_code="empty")
    if _PRIVATE_KEY_PATTERN.search(normalized):
        return KnowledgeRedactionResult(
            allowed=False, text="", reason_code="private_key_detected"
        )
    if _SECRET_PATTERN.search(normalized):
        return KnowledgeRedactionResult(allowed=False, text="", reason_code="secret_detected")
    if not allow_email and _EMAIL_PATTERN.search(normalized):
        return KnowledgeRedactionResult(allowed=False, text="", reason_code="pii_email_detected")
    return KnowledgeRedactionResult(allowed=True, text=normalized)
