"""Central security guard for secret-aware chat and tool execution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.tools.base import ToolCall

_TOKEN_LIKE_RE = re.compile(
    r"(?<!\S)"
    r"(?=[A-Za-z0-9]{20,}(?!\S))"
    r"(?=[A-Za-z0-9]*[A-Za-z])"
    r"(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9]+"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(token|api[_\s-]?key|secret|password|пароль|токен|секрет)\b\s*[:=]\s*([^\n\r]+)"
)
_NATURAL_SECRET_RE = re.compile(
    r"(?i)\b((?:my|your|our|мой|ваш|твой)?\s*"
    r"(?:token|api[_\s-]?key|secret|password|пароль|токен|секрет)\s*"
    r"(?:is|equals|это|равен))\s+([^\n\r]+)"
)
_KNOWN_SECRET_RE = re.compile(r"\b(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_SECRET_KEYWORD_RE = re.compile(
    r"(?i)\b(token|api[_\s-]?key|secret|password|пароль|токен|секрет|credential|credentials)\b"
)
_GENERIC_SECRET_RE = re.compile(
    r"(?<!\S)(?=[A-Za-z0-9_:\-]{24,}(?!\S))(?=[A-Za-z0-9_:\-]*[A-Za-z])(?=[A-Za-z0-9_:\-]*\d)[A-Za-z0-9_:\-]+"
)
_SENSITIVE_FIELD_PARTS = ("secret", "token", "password", "api_key", "authorization")
_SECURE_TOOL_FIELDS: dict[str, tuple[str, ...]] = {
    "credentials.create": ("value", "secret_value"),
    "credentials.update": ("value", "secret_value"),
    "credentials.request": ("value", "secret_value"),
}


@dataclass(slots=True)
class GuardDecision:
    """Decision for one user/assistant message."""

    allow: bool
    redacted_text: str
    error_code: str | None = None
    blocked_reason: str | None = None
    secure_field: str | None = None


@dataclass(slots=True)
class GuardedToolCall:
    """Decision for one planned tool call."""

    allow: bool
    execution_call: ToolCall
    log_call: ToolCall
    error_code: str | None = None
    blocked_reason: str | None = None


class SecurityGuard:
    """Apply chat/runtime secret constraints and redaction."""

    def check_user_message(self, *, text: str) -> GuardDecision:
        """Validate one incoming user message."""

        redacted = self.redact_text(text)
        if self._contains_secret(text):
            return GuardDecision(
                allow=False,
                redacted_text=redacted,
                error_code="security_secret_input_blocked",
                blocked_reason="Secret-like input detected in chat flow",
            )
        return GuardDecision(allow=True, redacted_text=redacted)

    def check_assistant_message(self, *, text: str) -> GuardDecision:
        """Validate one assistant message before persistence/output."""

        redacted = self.redact_text(text)
        if self._contains_secret(text):
            return GuardDecision(
                allow=False,
                redacted_text=redacted,
                error_code="security_secret_output_blocked",
                blocked_reason="Secret-like output blocked in chat flow",
            )
        return GuardDecision(allow=True, redacted_text=redacted)

    def guard_tool_call(self, *, call: ToolCall) -> GuardedToolCall:
        """Return execution/log variants and optional block decision."""

        raw_params = self._to_params_dict(call.params)
        redacted_params = self.redact_value(raw_params)
        if not isinstance(redacted_params, dict):
            redacted_params = {}
        secure_fields = _SECURE_TOOL_FIELDS.get(call.name)
        if secure_fields:
            for field_name in secure_fields:
                if field_name in redacted_params:
                    redacted_params[field_name] = "[REDACTED]"
        log_call = ToolCall(
            name=call.name,
            params={str(key): item for key, item in redacted_params.items()},
        )
        if secure_fields:
            for field_name in secure_fields:
                field_value = raw_params.get(field_name)
                if isinstance(field_value, str) and field_value.strip():
                    return GuardedToolCall(
                        allow=False,
                        execution_call=call,
                        log_call=log_call,
                        error_code="security_secure_input_required",
                        blocked_reason=(
                            f"Tool {call.name} requires request_secure_field flow for field {field_name}"
                        ),
                    )
        return GuardedToolCall(allow=True, execution_call=call, log_call=log_call)

    def redact_text(self, value: str) -> str:
        """Mask secret-like fragments in plain text."""

        text = _KNOWN_SECRET_RE.sub("[REDACTED]", value)
        text = _TELEGRAM_TOKEN_RE.sub("[REDACTED]", text)
        text = _ASSIGNMENT_SECRET_RE.sub(r"\1=[REDACTED]", text)
        text = _NATURAL_SECRET_RE.sub(r"\1 [REDACTED]", text)
        text = self._redact_contextual_matches(text, _GENERIC_SECRET_RE)
        return self._redact_contextual_matches(text, _TOKEN_LIKE_RE)

    @classmethod
    def redact_value(cls, value: object, *, field_name: str | None = None) -> object:
        """Mask secrets in nested payload structures."""

        if field_name is not None and cls._is_sensitive_field(field_name):
            return "[REDACTED]"
        if isinstance(value, str):
            return cls().redact_text(value)
        if value is None:
            return None
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, dict):
            return {
                str(key): cls.redact_value(item, field_name=str(key))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls.redact_value(item) for item in value]
        if isinstance(value, set):
            return [cls.redact_value(item) for item in value]
        return cls().redact_text(repr(value))

    @staticmethod
    def _contains_secret(text: str) -> bool:
        if _KNOWN_SECRET_RE.search(text):
            return True
        if _TELEGRAM_TOKEN_RE.search(text):
            return True
        for match in _ASSIGNMENT_SECRET_RE.finditer(text):
            if SecurityGuard._has_non_redacted_segment(match.group(2)):
                return True
        for match in _NATURAL_SECRET_RE.finditer(text):
            if SecurityGuard._has_non_redacted_segment(match.group(2)):
                return True
        for match in _GENERIC_SECRET_RE.finditer(text):
            if SecurityGuard._long_token_has_secret_context(
                text=text,
                token_start=match.start(),
                token_end=match.end(),
            ):
                return True
        for match in _TOKEN_LIKE_RE.finditer(text):
            if SecurityGuard._long_token_has_secret_context(
                text=text,
                token_start=match.start(),
                token_end=match.end(),
            ):
                return True
        return False

    @staticmethod
    def _long_token_has_secret_context(
        *,
        text: str,
        token_start: int,
        token_end: int,
    ) -> bool:
        """Return whether a long token is likely secret-like by nearby context."""

        window_start = max(0, token_start - 60)
        window_end = min(len(text), token_end + 60)
        window = text[window_start:window_end]
        if _SECRET_KEYWORD_RE.search(window):
            return True
        if re.search(r"(?i)\b(?:key|password|token)\b", window):
            return True
        if re.search(r"(?<!\S)\w{0,1}[:=]\s*[A-Za-z0-9_:\-]{24,}(?!\S)", window):
            return True
        return False

    @classmethod
    def _redact_contextual_matches(
        cls,
        text: str,
        pattern: re.Pattern[str],
    ) -> str:
        """Redact long tokens only when nearby context marks them as secrets."""

        return pattern.sub(lambda match: cls._contextual_redaction(text, match), text)

    @classmethod
    def _contextual_redaction(
        cls,
        text: str,
        match: re.Match[str],
    ) -> str:
        """Return one match or a redacted marker based on surrounding context."""

        if cls._long_token_has_secret_context(
            text=text,
            token_start=match.start(),
            token_end=match.end(),
        ):
            return "[REDACTED]"
        return match.group(0)

    @staticmethod
    def _has_non_redacted_segment(raw_value: str) -> bool:
        tokens = [token.strip(" \t,;:.!?)(") for token in raw_value.split()]
        for token in tokens:
            if not token:
                continue
            if token == "[REDACTED]":
                continue
            return True
        return False

    @staticmethod
    def _is_sensitive_field(field_name: str) -> bool:
        lowered = field_name.lower()
        return any(part in lowered for part in _SENSITIVE_FIELD_PARTS)

    @staticmethod
    def _to_params_dict(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items()}
