"""Unit tests for automation cron validators."""

from __future__ import annotations

from datetime import datetime

import pytest

from afkbot.services.automations.errors import AutomationsServiceError
from afkbot.services.automations.validators import (
    compute_next_run_at,
    normalize_cron_expr,
    normalize_timezone_name,
)


def test_compute_next_run_at_supports_every_five_minutes() -> None:
    """Step-based minute cron should schedule at the next matching minute boundary."""

    now = datetime.fromisoformat("2026-03-12T15:59:59.325521+00:00")
    assert compute_next_run_at("*/5 * * * *", now, "UTC") == datetime.fromisoformat(
        "2026-03-12T16:00:00+00:00"
    )


def test_compute_next_run_at_supports_hourly_and_daily_boundaries() -> None:
    """Exact minute/hour cron should snap to the next correct boundary."""

    now = datetime.fromisoformat("2026-03-12T15:59:59.325521+00:00")
    assert compute_next_run_at("0 * * * *", now, "UTC") == datetime.fromisoformat(
        "2026-03-12T16:00:00+00:00"
    )
    assert compute_next_run_at("15 9 * * *", now, "UTC") == datetime.fromisoformat(
        "2026-03-13T09:15:00+00:00"
    )


def test_compute_next_run_at_respects_iana_timezone() -> None:
    """Daily cron should be evaluated in the requested local timezone, not raw UTC."""

    now = datetime.fromisoformat("2026-03-12T15:59:59+00:00")
    assert compute_next_run_at("0 9 * * *", now, "Europe/Berlin") == datetime.fromisoformat(
        "2026-03-13T08:00:00+00:00"
    )


def test_normalize_cron_expr_rejects_unsupported_syntax() -> None:
    """Unsupported cron fields should fail with one structured error."""

    with pytest.raises(AutomationsServiceError) as exc_info:
        normalize_cron_expr("@daily")
    assert exc_info.value.error_code == "invalid_cron_expr"


def test_normalize_timezone_name_rejects_unknown_iana_timezone() -> None:
    """Invalid timezone names should fail validation instead of being persisted verbatim."""

    with pytest.raises(AutomationsServiceError) as exc_info:
        normalize_timezone_name("Mars/OlympusMons")
    assert exc_info.value.error_code == "invalid_timezone"


def test_compute_next_run_at_requires_aware_datetime() -> None:
    """Naive timestamps should still fail upstream when cron runtime uses them incorrectly."""

    with pytest.raises(ValueError):
        compute_next_run_at("* * * * *", datetime(2026, 3, 12, 15, 59, 59), "UTC")
