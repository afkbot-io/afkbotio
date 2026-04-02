"""Contracts for LLM-driven planning and tool-calling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from afkbot.services.llm.reasoning import ReasoningEffort


class ToolCallRequest(BaseModel):
    """One tool invocation request emitted by an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    params: dict[str, object] = Field(default_factory=dict)
    call_id: str | None = None


class LLMToolDefinition(BaseModel):
    """One tool definition exposed to an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters_schema: dict[str, object] = Field(default_factory=dict)
    required_skill: str | None = None
    requires_confirmation: bool = False


class LLMMessage(BaseModel):
    """One chat message passed between AgentLoop and the LLM provider."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    provider_items: list[dict[str, object]] = Field(default_factory=list)


class LLMRequest(BaseModel):
    """Provider input payload for one completion request."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    session_id: str
    context: str
    history: list[LLMMessage] = Field(default_factory=list)
    available_tools: tuple[LLMToolDefinition, ...] = Field(default_factory=tuple)
    reasoning_effort: ReasoningEffort | None = None
    request_timeout_sec: float | None = Field(default=None, gt=0)


class LLMResponse(BaseModel):
    """Provider output envelope with either final text or tool calls."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["final", "tool_calls"]
    final_message: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    provider_items: list[dict[str, object]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_shape(self) -> LLMResponse:
        if self.kind == "final" and self.final_message is None:
            raise ValueError("final_message is required for final response")
        if self.kind == "tool_calls" and not self.tool_calls:
            raise ValueError("tool_calls is required for tool_calls response")
        if self.kind == "tool_calls" and self.error_code is not None:
            raise ValueError("error_code is not allowed for tool_calls response")
        if self.kind == "tool_calls" and self.error_detail is not None:
            raise ValueError("error_detail is not allowed for tool_calls response")
        return self

    @classmethod
    def final(
        cls,
        message: str,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        provider_items: list[dict[str, object]] | None = None,
    ) -> LLMResponse:
        """Build deterministic final response."""

        return cls(
            kind="final",
            final_message=message,
            error_code=error_code,
            error_detail=error_detail,
            provider_items=list(provider_items or []),
        )

    @classmethod
    def tool_calls_response(
        cls,
        calls: list[ToolCallRequest],
        *,
        provider_items: list[dict[str, object]] | None = None,
    ) -> LLMResponse:
        """Build structured tool-calls response."""

        return cls(
            kind="tool_calls",
            tool_calls=calls,
            provider_items=list(provider_items or []),
        )


class LLMProvider(Protocol):
    """Protocol for pluggable LLM providers."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return final message or tool calls for current turn context."""


class BaseLLMProvider(ABC):
    """Abstract base class for concrete LLM providers."""

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return final message or tool calls for current turn context."""
