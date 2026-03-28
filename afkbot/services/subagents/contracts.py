"""Contracts for subagent lifecycle and descriptors."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

SubagentLaunchMode = Literal["process", "inline"]
SubagentTaskStatus = Literal["running", "completed", "failed", "cancelled", "timeout"]


class SubagentInfo(BaseModel):
    """Resolved subagent descriptor metadata."""

    name: str
    path: Path
    origin: Literal["core", "profile"]


class SubagentRunAccepted(BaseModel):
    """Response for accepted subagent run request."""

    task_id: str
    status: Literal["running"]
    subagent_name: str
    timeout_sec: int


class SubagentWaitResponse(BaseModel):
    """Wait response for an existing subagent task."""

    task_id: str
    status: SubagentTaskStatus
    done: bool
    child_session_id: str | None = None
    child_run_id: int | None = None


class SubagentResultResponse(BaseModel):
    """Final or current result state for a subagent task."""

    task_id: str
    status: SubagentTaskStatus
    child_session_id: str | None = None
    child_run_id: int | None = None
    output: str | None = None
    error_code: str | None = None
    reason: str | None = None
