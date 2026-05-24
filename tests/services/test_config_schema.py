"""Tests for shared primitive JSON config schema validation."""

from __future__ import annotations

import pytest

from afkbot.services.config_schema import (
    normalize_json_config_fields,
    validate_json_config_payload,
)


def test_json_config_schema_applies_defaults_and_validates_types() -> None:
    payload = validate_json_config_payload(
        schema_fields={
            "poll_interval_sec": {
                "type": "integer",
                "minimum": 5,
                "maximum": 3600,
                "default": 30,
            },
            "mode": {
                "type": "string",
                "choices": ("poll", "webhook"),
                "default": "poll",
            },
        },
        payload={},
        config_label="channel endpoint",
    )

    assert payload == {"poll_interval_sec": 30, "mode": "poll"}


def test_json_config_schema_rejects_unknown_and_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="Unknown channel endpoint config keys: extra"):
        validate_json_config_payload(
            schema_fields={"poll_interval_sec": {"type": "integer", "default": 30}},
            payload={"extra": True},
            config_label="channel endpoint",
        )

    with pytest.raises(ValueError, match="Missing channel endpoint config key: api_base_url"):
        validate_json_config_payload(
            schema_fields={"api_base_url": {"type": "string"}},
            payload={},
            config_label="channel endpoint",
        )


def test_json_config_schema_rejects_invalid_field_specs_and_values() -> None:
    with pytest.raises(ValueError, match="minimum/maximum"):
        normalize_json_config_fields({"enabled": {"type": "boolean", "minimum": 1}})

    with pytest.raises(ValueError, match="poll_interval_sec"):
        validate_json_config_payload(
            schema_fields={"poll_interval_sec": {"type": "integer", "minimum": 5}},
            payload={"poll_interval_sec": 1},
            config_label="channel endpoint",
        )
