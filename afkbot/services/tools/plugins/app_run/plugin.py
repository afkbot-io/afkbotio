"""Tool plugin for app.run unified integration gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ConfigDict, Field, ValidationError

from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.params_validation import collect_validation_details
from afkbot.services.apps.registry import AppRegistry, get_app_registry
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters, ToolParametersValidationError, build_tool_parameters
from afkbot.settings import Settings


class AppRunParams(ToolParameters):
    """Parameters for app.run tool."""

    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=128)
    profile_name: str | None = Field(default=None, min_length=1, max_length=128)
    params: dict[str, object] = Field(default_factory=dict)


class AppRunTool(ToolBase):
    """Execute integration app actions through one unified tool."""

    name = "app.run"
    description = (
        "Run one integration action via unified app runtime. "
        "Required params: app_name, action. "
        "Optional params: params, profile_name. "
        "Use top-level field `params` for action arguments. "
        "profile_name is optional and auto-resolves to default/single credential profile. "
        "Runtime automatically routes the matching skill and credential workflow."
    )
    parameters_model = AppRunParams

    def __init__(self, settings: Settings, *, app_registry: AppRegistry | None = None) -> None:
        self._settings = settings
        self._app_registry = app_registry

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        """Validate params and emit structured LLM-friendly errors for app.run envelope fields."""

        try:
            return build_tool_parameters(
                self.parameters_model,
                raw_params,
                default_timeout_sec=default_timeout_sec,
                max_timeout_sec=max_timeout_sec,
            )
        except ValidationError as exc:
            raise _build_app_run_envelope_error(exc) from exc

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = AppRunParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        app_name = payload.app_name.strip().lower()
        if not app_name:
            return ToolResult.error(error_code="app_not_supported", reason="app_name is required")

        app_registry = self._app_registry or get_app_registry(
            settings=self._settings,
            profile_id=ctx.profile_id,
        )
        app_definition = app_registry.get(app_name)
        if app_definition is None:
            return ToolResult.error(
                error_code="app_not_supported",
                reason=f"Unsupported app: {app_name}",
            )

        context = AppRuntimeContext(
            profile_id=ctx.profile_id,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            credential_profile_key=payload.profile_name,
            timeout_sec=payload.timeout_sec,
        )
        runtime = AppRuntime(self._settings, app_registry=app_registry)
        return await runtime.run(
            app=app_name,
            action=payload.action,
            ctx=context,
            params=payload.params,
        )


def create_tool(settings: Settings) -> ToolBase:
    """Create app.run tool instance."""

    return AppRunTool(settings=settings)


def _build_app_run_envelope_error(exc: ValidationError) -> ToolParametersValidationError:
    """Build structured top-level app.run params error."""

    details = collect_validation_details(model=AppRunParams, exc=exc)
    reason_parts = ["Invalid top-level params for app.run."]
    if details.missing_params:
        reason_parts.append(f"Missing required fields: {', '.join(details.missing_params)}.")
    if details.unexpected_params:
        reason_parts.append(f"Unexpected fields: {', '.join(details.unexpected_params)}.")
    if details.invalid_params:
        formatted_invalid = "; ".join(
            f"{item['field']}: {item['message']}" for item in details.invalid_params
        )
        reason_parts.append(f"Invalid values: {formatted_invalid}.")
    reason_parts.append("Required top-level fields: app_name, action.")
    reason_parts.append("Optional top-level fields: profile_name, params, profile_id, profile_key, timeout_sec.")
    reason_parts.append("Use top-level `params` for action arguments.")
    return ToolParametersValidationError(
        error_code="app_run_invalid",
        reason=" ".join(reason_parts),
        metadata={
            "tool_name": "app.run",
            "required_fields": ["app_name", "action"],
            "optional_fields": ["params", "profile_name", "profile_id", "profile_key", "timeout_sec"],
            "missing_fields": details.missing_params,
            "unexpected_fields": details.unexpected_params,
            "invalid_fields": details.invalid_params,
            "allowed_fields": ["app_name", "action", "params", "profile_name", "profile_id", "profile_key", "timeout_sec"],
        },
    )
