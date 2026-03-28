"""Tests for tool parameter validation helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from afkbot.services.tools.params import ToolParameters, build_tool_parameters


class SampleParams(ToolParameters):
    """Sample params model for validation tests."""

    message: str


def test_build_tool_parameters_applies_default_timeout() -> None:
    """Helper should inject default timeout when it is missing."""

    params = build_tool_parameters(
        SampleParams,
        {"message": "hello"},
        default_timeout_sec=7,
        max_timeout_sec=10,
    )

    assert params.timeout_sec == 7
    assert params.message == "hello"


def test_build_tool_parameters_rejects_timeout_above_max() -> None:
    """Timeout over configured max should fail deterministically."""

    with pytest.raises(ValueError, match="timeout_sec must be <= 10"):
        build_tool_parameters(
            SampleParams,
            {"message": "hello", "timeout_sec": 11},
            default_timeout_sec=7,
            max_timeout_sec=10,
        )


def test_build_tool_parameters_rejects_unknown_fields() -> None:
    """Unexpected raw params should be rejected by strict model config."""

    with pytest.raises(ValidationError):
        build_tool_parameters(
            SampleParams,
            {"message": "hello", "unknown": "value"},
            default_timeout_sec=7,
            max_timeout_sec=10,
        )
