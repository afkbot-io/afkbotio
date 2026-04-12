"""Tool plugin for subagent.run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from afkbot.services.policy import PolicyViolationError
from afkbot.services.subagents import get_subagent_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters, build_tool_parameters
from afkbot.settings import Settings


class SubagentRunParams(ToolParameters):
    """Parameters for subagent.run tool."""

    prompt: str = Field(min_length=1, max_length=20_000)
    subagent_name: str | None = Field(default=None)


class SubagentRunTool(ToolBase):
    """Start a background subagent task."""

    name = "subagent.run"
    description = "Start subagent execution and return task id."
    parameters_model = SubagentRunParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        """Use subagent timeout settings for run tool constraints."""

        _ = default_timeout_sec, max_timeout_sec
        return build_tool_parameters(
            self.parameters_model,
            raw_params,
            default_timeout_sec=self._settings.subagent_timeout_default_sec,
            max_timeout_sec=self._settings.subagent_timeout_max_sec,
        )

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        run_params = SubagentRunParams.model_validate(params.model_dump())
        if ctx.actor == "subagent":
            return ToolResult.error(
                error_code="subagent_recursive_spawn_forbidden",
                reason="Subagent cannot spawn another subagent",
            )
        service = get_subagent_service(self._settings)
        try:
            accepted = await service.run(
                ctx=ctx,
                prompt=run_params.prompt,
                subagent_name=run_params.subagent_name,
                timeout_sec=run_params.timeout_sec,
            )
            return ToolResult(ok=True, payload=accepted.model_dump())
        except FileNotFoundError:
            return ToolResult.error(
                error_code="subagent_not_found",
                reason=f"Subagent not found: {run_params.subagent_name}",
            )
        except PermissionError as exc:
            return ToolResult.error(
                error_code="subagent_recursive_spawn_forbidden",
                reason=str(exc),
            )
        except PolicyViolationError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
            )
        except ValueError as exc:
            return ToolResult.error(
                error_code=_subagent_value_error_code(exc),
                reason=str(exc),
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create subagent.run tool instance."""

    return SubagentRunTool(settings=settings)


def _subagent_value_error_code(exc: ValueError) -> str:
    reason = str(exc).strip()
    if reason.startswith("Invalid subagent name:"):
        return "invalid_subagent_name"
    return "tool_params_invalid"
