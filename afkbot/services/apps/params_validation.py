"""Helpers for app action parameter validation errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from afkbot.services.tools.base import ToolResult


@dataclass(frozen=True, slots=True)
class ValidationDetails:
    """Normalized field-level validation summary for one Pydantic model."""

    required_params: list[str]
    optional_params: list[str]
    missing_params: list[str]
    unexpected_params: list[str]
    invalid_params: list[dict[str, str]]
    allowed_params: list[str]


def collect_validation_details(
    *,
    model: type[BaseModel],
    exc: ValidationError,
) -> ValidationDetails:
    """Extract required/optional/missing/unexpected field details from ValidationError."""

    required_params = sorted(
        field_name
        for field_name, field in model.model_fields.items()
        if field.is_required()
    )
    optional_params = sorted(
        field_name
        for field_name, field in model.model_fields.items()
        if not field.is_required()
    )

    missing_params: list[str] = []
    unexpected_params: list[str] = []
    invalid_params: list[dict[str, str]] = []
    for item in exc.errors():
        field_name = _format_error_location(item.get("loc"))
        error_type = str(item.get("type") or "").strip()
        message = str(item.get("msg") or "").strip()
        if error_type == "missing":
            if field_name:
                missing_params.append(field_name)
            continue
        if error_type == "extra_forbidden":
            if field_name:
                unexpected_params.append(field_name)
            continue
        invalid_params.append(
            {
                "field": field_name or "<root>",
                "message": message or error_type or "invalid value",
                "type": error_type or "invalid",
            }
        )

    missing_params = sorted(set(missing_params))
    unexpected_params = sorted(set(unexpected_params))
    invalid_params = sorted(invalid_params, key=lambda item: (item["field"], item["type"], item["message"]))
    allowed_params = required_params + optional_params
    return ValidationDetails(
        required_params=required_params,
        optional_params=optional_params,
        missing_params=missing_params,
        unexpected_params=unexpected_params,
        invalid_params=invalid_params,
        allowed_params=allowed_params,
    )


def build_app_params_validation_error(
    *,
    app_name: str,
    action: str,
    model: type[BaseModel],
    exc: ValidationError,
) -> ToolResult:
    """Convert pydantic validation details into LLM-friendly app.run error payload."""

    details = collect_validation_details(model=model, exc=exc)
    reason_parts = [f"Invalid params for {app_name}.{action}."]
    if details.missing_params:
        reason_parts.append(f"Missing required params: {', '.join(details.missing_params)}.")
    if details.unexpected_params:
        reason_parts.append(f"Unexpected params: {', '.join(details.unexpected_params)}.")
    if details.invalid_params:
        formatted_invalid = "; ".join(
            f"{item['field']}: {item['message']}" for item in details.invalid_params
        )
        reason_parts.append(f"Invalid values: {formatted_invalid}.")
    if details.required_params:
        reason_parts.append(f"Required params: {', '.join(details.required_params)}.")
    else:
        reason_parts.append("Required params: none.")
    if details.optional_params:
        reason_parts.append(f"Optional params: {', '.join(details.optional_params)}.")
    reason_parts.append("Pass action-specific fields inside app.run.params.")

    return ToolResult.error(
        error_code="app_run_invalid",
        reason=" ".join(reason_parts),
        metadata={
            "app_name": app_name,
            "action": action,
            "required_params": details.required_params,
            "optional_params": details.optional_params,
            "missing_params": details.missing_params,
            "unexpected_params": details.unexpected_params,
            "invalid_params": details.invalid_params,
            "allowed_params": details.allowed_params,
        },
    )


def _format_error_location(location: Any) -> str:
    if not isinstance(location, (tuple, list)):
        return ""
    parts = [str(part).strip() for part in location if str(part).strip()]
    return ".".join(parts)
