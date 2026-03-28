"""Shared timeout bounds for LLM request and provider transport flows."""

from __future__ import annotations

DEFAULT_LLM_REQUEST_TIMEOUT_SEC = 1800.0
MAX_LLM_REQUEST_TIMEOUT_SEC = 1800.0
DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC = 7200.0
MAX_LLM_WALL_CLOCK_BUDGET_SEC = 7200.0
MIN_LLM_REQUEST_TIMEOUT_SEC = 0.01


def clamp_llm_request_timeout_sec(value: float) -> float:
    """Clamp one LLM timeout value into the supported runtime range."""

    normalized = max(MIN_LLM_REQUEST_TIMEOUT_SEC, float(value))
    return min(normalized, MAX_LLM_REQUEST_TIMEOUT_SEC)


def resolve_llm_request_timeout_sec(
    value: float | None,
    *,
    fallback_sec: float,
) -> float:
    """Resolve optional per-request timeout against the configured fallback."""

    if value is None:
        return clamp_llm_request_timeout_sec(fallback_sec)
    return clamp_llm_request_timeout_sec(value)


def clamp_llm_wall_clock_budget_sec(value: float) -> float:
    """Clamp one total turn budget value into the supported runtime range."""

    normalized = max(MIN_LLM_REQUEST_TIMEOUT_SEC, float(value))
    return min(normalized, MAX_LLM_WALL_CLOCK_BUDGET_SEC)
