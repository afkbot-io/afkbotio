"""Progress-to-presentation mapping helpers for CLI rendering."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.agent_loop.progress_stream import CanonicalProgressStage, ProgressEvent


class RenderEvent(BaseModel):
    """UI-friendly progress event representation."""

    model_config = ConfigDict(extra="forbid")

    stage: CanonicalProgressStage
    iteration: int | None = Field(default=None, ge=0)
    tool_name: str | None = None
    event_type: str = Field(min_length=1)
    resumed_tool_call: bool = False
    live_result: bool = False


def map_progress_event(event: ProgressEvent) -> RenderEvent | None:
    """Map canonical progress event into render event shape."""

    if not event.event_type.strip():
        return None
    if event.stage in {"tool_call", "subagent_wait"} and event.event_type == "turn.progress":
        if event.tool_name is None:
            return None
    return RenderEvent(
        stage=event.stage,
        iteration=event.iteration,
        tool_name=event.tool_name,
        event_type=event.event_type,
        resumed_tool_call=is_resumed_tool_call(event),
        live_result=is_live_tool_result(event),
    )


def is_resumed_tool_call(event: ProgressEvent) -> bool:
    """Return whether one tool.call resumes an already-running interactive tool session."""

    if event.event_type != "tool.call":
        return False
    params = event.tool_call_params or {}
    session_id = str(params.get("session_id") or "").strip()
    return bool(session_id)


def is_live_tool_result(event: ProgressEvent) -> bool:
    """Return whether one tool.result is an intermediate live-session update."""

    if event.event_type != "tool.result":
        return False
    result = event.tool_result or {}
    payload = result.get("payload")
    return isinstance(payload, dict) and payload.get("running") is True
