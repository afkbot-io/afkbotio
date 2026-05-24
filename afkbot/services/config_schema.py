"""Small JSON config schema contract shared by operator-authored extensions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JsonConfigFieldType = Literal["string", "integer", "number", "boolean"]


class JsonConfigField(BaseModel):
    """One primitive JSON config field used by plugin and channel schemas."""

    model_config = ConfigDict(extra="forbid")

    type: JsonConfigFieldType
    title: str = ""
    description: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    required: bool = True
    default: object | None = None
    secret: bool = False

    @model_validator(mode="after")
    def _validate_constraints(self) -> "JsonConfigField":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must be less than or equal to maximum")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length must be less than or equal to max_length")
        if self.choices and self.type != "string":
            raise ValueError("choices are supported only for string config fields")
        if any(item is not None for item in (self.minimum, self.maximum)) and self.type not in {
            "integer",
            "number",
        }:
            raise ValueError("minimum/maximum are supported only for integer/number config fields")
        if (
            any(item is not None for item in (self.min_length, self.max_length, self.pattern))
            and self.type != "string"
        ):
            raise ValueError("string constraints are supported only for string config fields")
        if self.pattern is not None:
            re.compile(self.pattern)
        if self.default is not None:
            self.validate_value(key="default", value=self.default)
        return self

    def validate_value(self, *, key: str, value: object) -> object:
        """Validate one runtime config value against this field contract."""

        if self.type == "string":
            if not isinstance(value, str):
                raise ValueError(f"Config field '{key}' must be a string")
            if self.choices and value not in self.choices:
                raise ValueError(f"Config field '{key}' must be one of: {', '.join(self.choices)}")
            if self.min_length is not None and len(value) < self.min_length:
                raise ValueError(
                    f"Config field '{key}' must be at least {self.min_length} characters"
                )
            if self.max_length is not None and len(value) > self.max_length:
                raise ValueError(
                    f"Config field '{key}' must be at most {self.max_length} characters"
                )
            if self.pattern is not None and re.fullmatch(self.pattern, value) is None:
                raise ValueError(f"Config field '{key}' does not match the required pattern")
            return value
        if self.type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"Config field '{key}' must be a boolean")
            return value
        if self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Config field '{key}' must be an integer")
            return self._validate_numeric_value(key=key, value=value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Config field '{key}' must be a number")
        return self._validate_numeric_value(key=key, value=value)

    def _validate_numeric_value(self, *, key: str, value: int | float) -> int | float:
        numeric_value = float(value)
        if self.minimum is not None and numeric_value < self.minimum:
            raise ValueError(
                f"Config field '{key}' must be greater than or equal to {self.minimum}"
            )
        if self.maximum is not None and numeric_value > self.maximum:
            raise ValueError(f"Config field '{key}' must be less than or equal to {self.maximum}")
        return value


class JsonConfigSchema(BaseModel):
    """Object schema for primitive JSON config maps."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, JsonConfigField] = Field(default_factory=dict)


def normalize_json_config_fields(
    schema: Mapping[str, object] | JsonConfigSchema,
) -> dict[str, JsonConfigField]:
    """Normalize one field-map schema into typed field specs."""

    if isinstance(schema, JsonConfigSchema):
        return dict(schema.fields)
    fields: dict[str, JsonConfigField] = {}
    for key, raw_field in schema.items():
        field_key = str(key).strip()
        if not field_key:
            raise ValueError("Config schema field key cannot be empty")
        if isinstance(raw_field, JsonConfigField):
            fields[field_key] = raw_field
            continue
        if not isinstance(raw_field, Mapping):
            raise ValueError(f"Config schema field '{field_key}' must be an object")
        fields[field_key] = JsonConfigField.model_validate(dict(raw_field))
    return fields


def dump_json_config_fields(fields: Mapping[str, JsonConfigField]) -> dict[str, dict[str, object]]:
    """Return a JSON-serializable schema field map."""

    return {
        key: field.model_dump(mode="python", exclude_none=True)
        for key, field in fields.items()
    }


def validate_json_config_payload(
    *,
    schema_fields: Mapping[str, object] | JsonConfigSchema,
    payload: Mapping[str, object],
    config_label: str,
) -> dict[str, object]:
    """Validate and default one JSON config payload against a primitive field map."""

    fields = normalize_json_config_fields(schema_fields)
    validated = dict(payload)
    unknown = sorted(set(validated) - set(fields))
    if unknown:
        raise ValueError(f"Unknown {config_label} config keys: {', '.join(unknown)}")
    for key, field in fields.items():
        if key not in validated:
            if field.default is not None:
                validated[key] = field.default
            elif field.required:
                raise ValueError(f"Missing {config_label} config key: {key}")
            else:
                continue
        validated[key] = field.validate_value(key=key, value=validated[key])
    return validated
