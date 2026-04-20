"""Validation and normalization helpers for automation CRUD inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from afkbot.services.automations.errors import AutomationsServiceError

AutomationExecutionMode = Literal["prompt", "graph"]
AutomationGraphFallbackMode = Literal[
    "fail_closed",
    "resume_with_ai",
    "resume_with_ai_if_safe",
]


def validate_create_payload(*, name: str, prompt: str) -> None:
    """Validate required automation creation fields."""

    if not name.strip():
        raise AutomationsServiceError(error_code="invalid_name", reason="Name is required")
    if not prompt.strip():
        raise AutomationsServiceError(error_code="invalid_prompt", reason="Prompt is required")


def normalize_automation_prompt(
    prompt: str,
) -> str:
    """Return one stable automation prompt for persisted automation tasks."""

    return prompt.strip()


def normalize_update_status(status: str) -> Literal["active", "paused"]:
    """Normalize one update status value."""

    normalized = status.strip().lower()
    if normalized == "active":
        return "active"
    if normalized == "paused":
        return "paused"
    raise AutomationsServiceError(
        error_code="invalid_status",
        reason="Status must be active or paused",
    )


def normalize_execution_mode(value: str) -> AutomationExecutionMode:
    """Normalize one automation execution mode value."""

    normalized = value.strip().lower()
    if normalized == "prompt":
        return "prompt"
    if normalized == "graph":
        return "graph"
    raise AutomationsServiceError(
        error_code="invalid_execution_mode",
        reason="execution_mode must be prompt or graph",
    )


def normalize_graph_fallback_mode(value: str) -> AutomationGraphFallbackMode:
    """Normalize one graph fallback mode value."""

    normalized = value.strip().lower()
    if normalized in {"fail_closed", "resume_with_ai", "resume_with_ai_if_safe"}:
        return normalized  # type: ignore[return-value]
    raise AutomationsServiceError(
        error_code="invalid_graph_fallback_mode",
        reason=(
            "graph_fallback_mode must be one of: fail_closed, "
            "resume_with_ai, resume_with_ai_if_safe"
        ),
    )


def normalize_cron_expr(cron_expr: str) -> str:
    """Normalize one cron expression string."""

    normalized = cron_expr.strip()
    if not normalized:
        raise AutomationsServiceError(
            error_code="invalid_cron_expr",
            reason="Cron expression is required",
        )
    _parse_cron_fields(normalized)
    return normalized


def normalize_timezone_name(timezone_name: str) -> str:
    """Normalize one timezone name for persisted cron metadata."""

    normalized = timezone_name.strip() or "UTC"
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise AutomationsServiceError(
            error_code="invalid_timezone",
            reason=f"Unsupported timezone: {normalized}",
        ) from exc
    return normalized


def compute_next_run_at(cron_expr: str, now_utc: datetime, timezone_name: str) -> datetime:
    """Return the next UTC execution time for one normalized 5-field cron expression."""

    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    normalized_now = now_utc.astimezone(timezone.utc)
    schedule_timezone = ZoneInfo(normalize_timezone_name(timezone_name))
    minute_values, hour_values, day_values, month_values, weekday_values = _parse_cron_fields(
        cron_expr.strip()
    )
    candidate_utc = normalized_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        candidate = candidate_utc.astimezone(schedule_timezone)
        if candidate.month not in month_values:
            candidate_utc += timedelta(minutes=1)
            continue
        if not _matches_day(
            candidate=candidate,
            day_values=day_values,
            weekday_values=weekday_values,
        ):
            candidate_utc += timedelta(minutes=1)
            continue
        if candidate.hour not in hour_values:
            candidate_utc += timedelta(minutes=1)
            continue
        if candidate.minute in minute_values:
            return candidate_utc
        candidate_utc += timedelta(minutes=1)
    raise AutomationsServiceError(
        error_code="invalid_cron_expr",
        reason="Cron expression produced no execution time within one year",
    )


def _matches_day(
    *,
    candidate: datetime,
    day_values: set[int],
    weekday_values: set[int],
) -> bool:
    """Apply standard cron day-of-month/day-of-week matching semantics."""

    day_is_any = len(day_values) == 31
    weekday_is_any = len(weekday_values) == 7
    day_match = candidate.day in day_values
    cron_weekday = (candidate.weekday() + 1) % 7
    weekday_match = cron_weekday in weekday_values
    if day_is_any and weekday_is_any:
        return True
    if day_is_any:
        return weekday_match
    if weekday_is_any:
        return day_match
    return day_match or weekday_match


def _parse_cron_fields(
    cron_expr: str,
) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse one 5-field cron expression into concrete value sets."""

    fields = cron_expr.split()
    if len(fields) != 5:
        raise AutomationsServiceError(
            error_code="invalid_cron_expr",
            reason="Cron expression must contain exactly 5 fields",
        )
    minute, hour, day, month, weekday = fields
    return (
        _parse_field(field=minute, minimum=0, maximum=59, label="minute"),
        _parse_field(field=hour, minimum=0, maximum=23, label="hour"),
        _parse_field(field=day, minimum=1, maximum=31, label="day_of_month"),
        _parse_field(field=month, minimum=1, maximum=12, label="month"),
        _parse_field(field=weekday, minimum=0, maximum=7, label="day_of_week", map_seven_to_zero=True),
    )


def _parse_field(
    *,
    field: str,
    minimum: int,
    maximum: int,
    label: str,
    map_seven_to_zero: bool = False,
) -> set[int]:
    """Parse one cron field supporting wildcards, ranges, lists, and steps."""

    values: set[int] = set()
    for raw_part in field.split(","):
        part = raw_part.strip()
        if not part:
            raise _cron_field_error(label=label, field=field)
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                raise _cron_field_error(label=label, field=field)
            step = int(step_text)
        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise _cron_field_error(label=label, field=field)
            start = int(start_text)
            end = int(end_text)
        else:
            if not base.isdigit():
                raise _cron_field_error(label=label, field=field)
            start = int(base)
            end = int(base)
        if start < minimum or end > maximum or start > end:
            raise _cron_field_error(label=label, field=field)
        for value in range(start, end + 1, step):
            normalized_value = 0 if map_seven_to_zero and value == 7 else value
            values.add(normalized_value)
    if not values:
        raise _cron_field_error(label=label, field=field)
    return values


def _cron_field_error(*, label: str, field: str) -> AutomationsServiceError:
    """Build one structured invalid-cron error for field parsing."""

    return AutomationsServiceError(
        error_code="invalid_cron_expr",
        reason=f"Unsupported cron {label} field: {field}",
    )
