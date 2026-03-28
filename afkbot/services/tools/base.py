"""Core contracts for tools execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.tools.params import ToolParameters, build_tool_parameters


class ToolCall(BaseModel):
    """Planned tool invocation payload for one step in AgentLoop."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Execution context shared with every tool invocation."""

    profile_id: str
    session_id: str
    run_id: int
    actor: Literal["main", "subagent"] = "main"
    runtime_metadata: dict[str, object] | None = None
    progress_callback: ToolProgressCallback | None = None


class ToolResult(BaseModel):
    """Canonical structured result returned by tools."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    payload: dict[str, object] = Field(default_factory=dict)
    error_code: str | None = None
    reason: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def error(
        cls,
        *,
        error_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> ToolResult:
        """Create an error result with deterministic shape."""

        return cls(
            ok=False,
            error_code=error_code,
            reason=reason,
            metadata={} if metadata is None else metadata,
        )


TParams = TypeVar("TParams", bound=ToolParameters)
ToolProgressCallback = Callable[[dict[str, object]], Awaitable[None]]


class ToolBase(ABC):
    """Abstract base class for all registry-driven tool plugins."""

    name: ClassVar[str]
    description: ClassVar[str]
    parameters_model: ClassVar[type[ToolParameters]] = ToolParameters
    required_credentials: ClassVar[tuple[str, ...]] = ()
    required_skill: ClassVar[str | None] = None
    requires_automation_intent: ClassVar[bool] = False

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        """Validate and normalize params for this tool plugin."""

        return build_tool_parameters(
            self.parameters_model,
            raw_params,
            default_timeout_sec=default_timeout_sec,
            max_timeout_sec=max_timeout_sec,
        )

    def llm_parameters_schema(self) -> dict[str, object]:
        """Return the LLM-visible parameters schema for this tool."""

        return {
            str(key): value for key, value in self.parameters_model.model_json_schema().items()
        }

    @staticmethod
    def _coerce_params(params: ToolParameters, expected: type[TParams]) -> TParams:
        """Re-validate one generic ToolParameters payload into one concrete params model."""

        return expected.model_validate(params.model_dump())

    @staticmethod
    def _ensure_profile_scope(ctx: ToolContext, payload: ToolParameters) -> ToolResult | None:
        """Return deterministic profile scope error when payload/profile mismatch is detected."""

        if payload.effective_profile_id == ctx.profile_id:
            return None
        return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

    def _prepare_params(
        self,
        *,
        ctx: ToolContext,
        params: ToolParameters,
        expected: type[TParams],
    ) -> TParams | ToolResult:
        """Run shared tool preamble: normalize concrete params and enforce profile scope."""

        payload = self._coerce_params(params=params, expected=expected)
        scope_error = self._ensure_profile_scope(ctx=ctx, payload=payload)
        if scope_error is not None:
            return scope_error
        return payload

    def policy_params(
        self,
        raw_params: Mapping[str, object],
        *,
        ctx: ToolContext | None = None,
    ) -> dict[str, object]:
        """Return params shape that should be evaluated by policy and safety layers."""

        _ = ctx
        return {str(key): value for key, value in raw_params.items()}

    @abstractmethod
    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        """Execute tool with validated params and explicit execution context."""
