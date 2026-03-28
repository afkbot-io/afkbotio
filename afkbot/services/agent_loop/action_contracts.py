"""Action envelope contracts for the agent loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ActionType = Literal["ask_question", "request_secure_field", "update_spec", "block", "finalize"]


class ActionEnvelope(BaseModel):
    """Single action envelope emitted by the loop."""

    action: ActionType
    message: str
    question_id: str | None = None
    spec_patch: dict[str, object] | None = None
    secure_field: str | None = None
    blocked_reason: str | None = None


class TurnResult(BaseModel):
    """Deterministic response for one completed agent turn."""

    run_id: int
    session_id: str
    profile_id: str
    envelope: ActionEnvelope
