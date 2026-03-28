"""Request and response payloads for chat HTTP adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.agent_loop.action_contracts import TurnResult


class ChatTurnRequest(BaseModel):
    """REST request payload for one chat turn execution."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    resolve_binding: bool = False
    require_binding_match: bool = False
    transport: str | None = Field(default=None, min_length=1)
    account_id: str | None = Field(default=None, min_length=1)
    peer_id: str | None = Field(default=None, min_length=1)
    thread_id: str | None = Field(default=None, min_length=1)
    user_id: str | None = Field(default=None, min_length=1)
    client_msg_id: str | None = Field(default=None, min_length=1, max_length=128)
    plan_only: bool = False
    planning_mode: Literal["off", "auto", "on"] | None = None
    thinking_level: str | None = Field(default=None, min_length=1)


class SecureFieldSubmitRequest(BaseModel):
    """REST payload for secure credential value submission."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    resolve_binding: bool = False
    require_binding_match: bool = False
    transport: str | None = Field(default=None, min_length=1)
    account_id: str | None = Field(default=None, min_length=1)
    peer_id: str | None = Field(default=None, min_length=1)
    thread_id: str | None = Field(default=None, min_length=1)
    user_id: str | None = Field(default=None, min_length=1)
    question_id: str = Field(min_length=1)
    secure_field: str = Field(min_length=1)
    secret_value: str = Field(min_length=1)
    spec_patch: dict[str, object] = Field(default_factory=dict)
    resume_after_submit: bool = False
    client_msg_id: str | None = Field(default=None, min_length=1, max_length=128)


class SecureFieldSubmitResponse(BaseModel):
    """Secure credential submit operation result."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    error_code: str
    next_turn: TurnResult | None = None


class QuestionAnswerRequest(BaseModel):
    """REST payload for one ask-question approval or profile-selection answer."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    resolve_binding: bool = False
    require_binding_match: bool = False
    transport: str | None = Field(default=None, min_length=1)
    account_id: str | None = Field(default=None, min_length=1)
    peer_id: str | None = Field(default=None, min_length=1)
    thread_id: str | None = Field(default=None, min_length=1)
    user_id: str | None = Field(default=None, min_length=1)
    question_id: str = Field(min_length=1)
    approved: bool | None = None
    answer: str | None = Field(default=None, min_length=1)
    spec_patch: dict[str, object] = Field(default_factory=dict)
    client_msg_id: str | None = Field(default=None, min_length=1, max_length=128)


__all__ = [
    "ChatTurnRequest",
    "QuestionAnswerRequest",
    "SecureFieldSubmitRequest",
    "SecureFieldSubmitResponse",
]
