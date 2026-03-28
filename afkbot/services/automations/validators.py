"""Validation and normalization helpers for automation CRUD inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, cast

from afkbot.services.automations.errors import AutomationsServiceError


def validate_create_payload(*, name: str, prompt: str) -> None:
    """Validate required automation creation fields."""

    if not name.strip():
        raise AutomationsServiceError(error_code="invalid_name", reason="Name is required")
    if not prompt.strip():
        raise AutomationsServiceError(error_code="invalid_prompt", reason="Prompt is required")


def normalize_automation_prompt(
    prompt: str,
    *,
    delivery_mode: Literal["target", "tool", "none"],
) -> str:
    """Return one stable automation prompt with delivery-mode execution hints."""

    normalized = _strip_managed_execution_hints(prompt.strip())
    if delivery_mode != "tool":
        return normalized
    hint_lines = [
        "Execution hints:",
        "- Use available tools directly to perform the requested side effects.",
        "- Do not rely on a platform delivery target unless one is explicitly configured.",
    ]
    prompt_lower = normalized.lower()
    if _mentions_any(prompt_lower, ("telegram", "телеграм")):
        hint_lines.append(
            "- Use app.run with the Telegram app for sending messages or actions; if the task does not specify chat_id explicitly, use the configured default Telegram credentials."
        )
    if _mentions_any(prompt_lower, ("smtp", "email", "почт", "mail")):
        hint_lines.append(
            "- Use app.run with the SMTP app for outbound email delivery when email is part of the task."
        )
    if _mentions_any(prompt_lower, ("imap", "почт", "mail", "email")):
        hint_lines.append(
            "- Use app.run with the IMAP app when the task requires reading mailbox contents."
        )
    if _mentions_any(prompt_lower, ("api", "http", "https", "webhook", "post ", "get ", "patch ", "put ", "delete ")):
        hint_lines.append("- Use http.request for external HTTP or webhook calls.")
    if _mentions_any(prompt_lower, ("bash", "shell", "command", "script", "скрипт", "команд")):
        hint_lines.append("- Use bash.exec for shell command execution when the task needs it.")
    hint_block = "\n".join(hint_lines)
    if hint_block in normalized:
        return normalized
    return f"{normalized}\n\n{hint_block}"


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

    return timezone_name.strip() or "UTC"


def normalize_delivery_mode(
    delivery_mode: str | None,
    *,
    has_delivery_target: bool,
) -> Literal["target", "tool", "none"]:
    """Normalize automation delivery mode with target-aware defaults."""

    if delivery_mode is None:
        return "target" if has_delivery_target else "tool"
    normalized = delivery_mode.strip().lower()
    if normalized not in {"target", "tool", "none"}:
        raise AutomationsServiceError(
            error_code="invalid_delivery_mode",
            reason="delivery_mode must be target, tool, or none",
        )
    if normalized == "target" and not has_delivery_target:
        raise AutomationsServiceError(
            error_code="invalid_delivery_mode",
            reason="delivery_mode=target requires delivery_target",
        )
    return cast(Literal["target", "tool", "none"], normalized)


def _mentions_any(text: str, needles: tuple[str, ...]) -> bool:
    """Return whether one lowercase text contains any lowercase needle."""

    return any(needle in text for needle in needles)


def _strip_managed_execution_hints(prompt: str) -> str:
    """Remove one previously appended managed execution-hints block from prompt tail."""

    marker = "\n\nExecution hints:"
    index = prompt.rfind(marker)
    if index == -1:
        return prompt
    return prompt[:index].rstrip()


def compute_next_run_at(cron_expr: str, now_utc: datetime) -> datetime:
    """Return the next UTC execution time for one normalized 5-field cron expression."""

    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    normalized_now = now_utc.astimezone(timezone.utc)
    minute_values, hour_values, day_values, month_values, weekday_values = _parse_cron_fields(
        cron_expr.strip()
    )
    candidate = normalized_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if candidate.month not in month_values:
            candidate += timedelta(minutes=1)
            continue
        if not _matches_day(
            candidate=candidate,
            day_values=day_values,
            weekday_values=weekday_values,
        ):
            candidate += timedelta(minutes=1)
            continue
        if candidate.hour not in hour_values:
            candidate += timedelta(minutes=1)
            continue
        if candidate.minute in minute_values:
            return candidate
        candidate += timedelta(minutes=1)
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
