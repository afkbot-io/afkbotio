"""Debug tool plugin that echoes user-provided message."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class DebugEchoParams(ToolParameters):
    """Parameters for debug echo plugin."""

    message: str = Field(min_length=1, max_length=2048)


class DebugEchoTool(ToolBase):
    """Simple deterministic tool for integration and contract tests."""

    name = "debug.echo"
    description = "Echo input message for tool pipeline validation."
    parameters_model = DebugEchoParams

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        """Return deterministic payload with echoed message and execution context."""

        echo_params = DebugEchoParams.model_validate(params.model_dump())
        return ToolResult(
            ok=True,
            payload={
                "message": echo_params.message,
                "timeout_sec": echo_params.timeout_sec,
                "profile_id": ctx.profile_id,
            },
        )


def create_tool(settings: Settings) -> ToolBase:
    """Create debug echo tool instance."""

    _ = settings
    return DebugEchoTool()
