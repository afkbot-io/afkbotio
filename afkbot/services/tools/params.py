"""Tool parameter contracts and validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import model_validator


class ToolParameters(BaseModel):
    """Common parameters accepted by every tool plugin."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    profile_key: str | None = None
    timeout_sec: int = Field(default=15, ge=1)

    @model_validator(mode="after")
    def _normalize_profile_fields(self) -> "ToolParameters":
        profile_id = (self.profile_id or "").strip()
        profile_key = (self.profile_key or "").strip()
        if profile_id and profile_key and profile_id != profile_key:
            raise ValueError("profile_id and profile_key must match when both are provided")
        resolved = profile_id or profile_key or "default"
        self.profile_id = resolved
        self.profile_key = resolved
        return self

    @property
    def effective_profile_id(self) -> str:
        """Return normalized profile identifier."""

        return self.profile_id or "default"


class RoutedToolParameters(ToolParameters):
    """Base params for tools that are exposed only through routed skills."""

    pass


class AppToolParameters(ToolParameters):
    """Common parameters for integration tools requiring credential profile selection."""

    credential_profile_key: str = Field(default="default", min_length=1, max_length=128)


TToolParameters = TypeVar("TToolParameters", bound=ToolParameters)


class ToolParametersValidationError(ValueError):
    """Structured validation error for tool params parsing."""

    def __init__(
        self,
        *,
        reason: str,
        error_code: str = "tool_params_invalid",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.metadata = {} if metadata is None else dict(metadata)


def build_tool_parameters(
    parameters_model: type[TToolParameters],
    raw_params: Mapping[str, object],
    *,
    default_timeout_sec: int,
    max_timeout_sec: int,
) -> TToolParameters:
    """Validate raw tool params and enforce timeout policy constraints."""

    payload: dict[str, object] = dict(raw_params)
    payload.setdefault("timeout_sec", default_timeout_sec)
    params = parameters_model.model_validate(payload)
    if params.timeout_sec > max_timeout_sec:
        raise ValueError(f"timeout_sec must be <= {max_timeout_sec}, got {params.timeout_sec}")
    return params
