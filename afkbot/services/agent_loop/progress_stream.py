"""Read-only progress polling service for CLI/API presentation layers."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogEventRead, RunlogRepository

CanonicalProgressStage = Literal[
    "thinking",
    "planning",
    "tool_call",
    "subagent_wait",
    "done",
    "cancelled",
]

_PROGRESS_STAGE_MAPPING: dict[str, CanonicalProgressStage] = {
    "thinking": "thinking",
    "planning": "planning",
    "tool_executing": "tool_call",
    "llm_iteration": "thinking",
    # Forward-compatible pass-through values.
    "tool_call": "tool_call",
    "subagent_wait": "subagent_wait",
    "done": "done",
    "cancelled": "cancelled",
}


class ProgressCursor(BaseModel):
    """Progress stream cursor."""

    model_config = ConfigDict(extra="forbid")

    run_id: int | None = None
    last_event_id: int = Field(default=0, ge=0)


class ProgressEvent(BaseModel):
    """Canonical progress event used by CLI presentation."""

    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    run_id: int = Field(ge=1)
    stage: CanonicalProgressStage
    iteration: int | None = Field(default=None, ge=0)
    tool_name: str | None = None
    call_id: str | None = None
    event_type: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)
    _tool_call_params: dict[str, object] | None = PrivateAttr(default=None)
    _tool_progress: dict[str, object] | None = PrivateAttr(default=None)
    _tool_result: dict[str, object] | None = PrivateAttr(default=None)

    def attach_tool_details(
        self,
        *,
        tool_call_params: dict[str, object] | None = None,
        tool_progress: dict[str, object] | None = None,
        tool_result: dict[str, object] | None = None,
    ) -> None:
        """Attach non-serialized tool payload details for CLI rendering."""

        if tool_call_params is not None:
            self._tool_call_params = {str(key): value for key, value in tool_call_params.items()}
        if tool_progress is not None:
            self._tool_progress = {str(key): value for key, value in tool_progress.items()}
        if tool_result is not None:
            self._tool_result = {str(key): value for key, value in tool_result.items()}

    @property
    def tool_call_params(self) -> dict[str, object] | None:
        """Return attached tool-call params copy when available."""

        if self._tool_call_params is None:
            return None
        return {str(key): value for key, value in self._tool_call_params.items()}

    @property
    def tool_result(self) -> dict[str, object] | None:
        """Return attached tool-result payload copy when available."""

        if self._tool_result is None:
            return None
        return {str(key): value for key, value in self._tool_result.items()}

    @property
    def tool_progress(self) -> dict[str, object] | None:
        """Return attached tool-progress payload copy when available."""

        if self._tool_progress is None:
            return None
        return {str(key): value for key, value in self._tool_progress.items()}


class ProgressStream:
    """Read-only polling service over runlog events for one chat session."""

    def __init__(self, session: AsyncSession, *, batch_size: int = 50) -> None:
        self._run_repo = RunRepository(session)
        self._runlog_repo = RunlogRepository(session)
        self._batch_size = max(1, batch_size)

    async def poll(
        self,
        *,
        profile_id: str,
        session_id: str,
        cursor: ProgressCursor,
    ) -> tuple[list[ProgressEvent], ProgressCursor]:
        """Poll next progress events for cursor-bound run (or latest when unset)."""

        run_id = cursor.run_id
        if run_id is None:
            run_id = await self.resolve_latest_run_id(profile_id=profile_id, session_id=session_id)
            if run_id is None:
                return [], cursor
        else:
            owned = await self._run_repo.is_run_owned_by_session(
                run_id=run_id,
                profile_id=profile_id,
                session_id=session_id,
            )
            if not owned:
                return [], cursor

        rows = await self._runlog_repo.list_run_events_since(
            run_id=run_id,
            after_event_id=cursor.last_event_id,
            limit=self._batch_size,
        )
        events: list[ProgressEvent] = []
        for row in rows:
            mapped = self._map_event(row)
            if mapped is not None:
                events.append(mapped)

        last_event_id = cursor.last_event_id
        if rows:
            last_event_id = max(last_event_id, rows[-1].id)

        return events, ProgressCursor(run_id=run_id, last_event_id=last_event_id)

    async def resolve_latest_run_id(self, *, profile_id: str, session_id: str) -> int | None:
        """Resolve latest run id for profile/session pair."""

        return await self._run_repo.get_latest_run_id(
            profile_id=profile_id,
            session_id=session_id,
        )

    @classmethod
    def _map_event(cls, event: RunlogEventRead) -> ProgressEvent | None:
        payload = cls._load_payload(event.payload_json)
        stage = cls._resolve_stage(event_type=event.event_type, payload=payload)
        if stage is None:
            return None
        progress = ProgressEvent(
            event_id=event.id,
            run_id=event.run_id,
            stage=stage,
            iteration=cls._resolve_iteration(payload),
            tool_name=cls._resolve_tool_name(event_type=event.event_type, payload=payload),
            call_id=cls._resolve_call_id(payload),
            event_type=event.event_type,
            payload=payload,
        )
        progress.attach_tool_details(
            tool_call_params=cls._resolve_tool_call_params(
                event_type=event.event_type,
                payload=payload,
            ),
            tool_progress=cls._resolve_tool_progress(
                event_type=event.event_type,
                payload=payload,
            ),
            tool_result=cls._resolve_tool_result(
                event_type=event.event_type,
                payload=payload,
            ),
        )
        return progress

    @staticmethod
    def _load_payload(payload_json: str) -> dict[str, object]:
        try:
            raw = json.loads(payload_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items()}

    @classmethod
    def _resolve_stage(
        cls,
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> CanonicalProgressStage | None:
        if event_type == "turn.think":
            return "thinking"
        if event_type == "turn.plan":
            planning_mode = str(payload.get("planning_mode") or "").strip().lower()
            return "planning" if planning_mode == "plan_only" else None
        if event_type == "turn.finalize":
            return "done"
        if event_type == "turn.cancel":
            return "cancelled"
        if event_type in {"tool.call", "tool.progress", "tool.result"}:
            tool_name = cls._resolve_tool_name(event_type=event_type, payload=payload)
            if cls._is_subagent_wait(tool_name):
                return "subagent_wait"
            return "tool_call"
        if event_type.startswith("llm.call."):
            return "thinking"
        if event_type == "turn.progress":
            raw_stage = payload.get("stage")
            if not isinstance(raw_stage, str):
                return None
            return _PROGRESS_STAGE_MAPPING.get(raw_stage.strip().lower())
        return None

    @staticmethod
    def _resolve_iteration(payload: dict[str, object]) -> int | None:
        raw = payload.get("iteration")
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return None
            try:
                parsed = int(stripped)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    def _resolve_tool_name(*, event_type: str, payload: dict[str, object]) -> str | None:
        if event_type not in {"tool.call", "tool.progress", "tool.result", "turn.progress"}:
            return None

        raw_name = payload.get("name")
        if raw_name is None:
            raw_name = payload.get("tool_name")
        if not isinstance(raw_name, str):
            return None

        normalized = raw_name.strip()
        return normalized or None

    @staticmethod
    def _resolve_call_id(payload: dict[str, object]) -> str | None:
        raw_call_id = payload.get("call_id")
        if not isinstance(raw_call_id, str):
            return None
        normalized = raw_call_id.strip()
        return normalized or None

    @staticmethod
    def _resolve_tool_call_params(
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        if event_type != "tool.call":
            return None
        raw = payload.get("params")
        if not isinstance(raw, dict):
            return None
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _resolve_tool_result(
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        if event_type != "tool.result":
            return None
        raw = payload.get("result")
        if not isinstance(raw, dict):
            return None
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _resolve_tool_progress(
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> dict[str, object] | None:
        if event_type != "tool.progress":
            return None
        raw = payload.get("progress")
        if not isinstance(raw, dict):
            return None
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _is_subagent_wait(tool_name: str | None) -> bool:
        if tool_name is None:
            return False
        normalized = tool_name.strip().lower()
        return normalized in {"subagent.wait", "subagent_wait"}
