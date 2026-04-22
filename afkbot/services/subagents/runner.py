"""Execution runner for one subagent task."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from contextlib import suppress
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.repositories.subagent_task_repo import SubagentTaskRepository
from afkbot.settings import Settings
from afkbot.services.subagents.orchestration import (
    build_subagent_session_orchestrator,
    resolve_subagent_loop_settings,
)
from afkbot.services.subagents.runtime_policy import DEFAULT_SUBAGENT_RUNTIME_POLICY, SubagentRuntimePolicy

_CANCEL_POLL_SEC = 0.2
_CANCEL_GRACE_SEC = 5.0


@dataclass(frozen=True, slots=True)
class SubagentExecutionResult:
    """Final child-agent output plus trace metadata."""

    output: str
    child_session_id: str
    child_run_id: int


class SubagentExecutionError(RuntimeError):
    """Structured subagent execution failure surfaced to task persistence layer."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class SubagentRunner:
    """Run one subagent task through a child AgentLoop session."""

    def __init__(
        self,
        settings: Settings,
        runtime_policy: SubagentRuntimePolicy = DEFAULT_SUBAGENT_RUNTIME_POLICY,
    ) -> None:
        self._settings = settings
        self._runtime_policy = runtime_policy

    async def execute(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        task_id: str,
        profile_id: str,
        parent_session_id: str,
        subagent_name: str,
        subagent_markdown: str,
        prompt: str,
    ) -> SubagentExecutionResult:
        """Execute subagent prompt through isolated child-agent runtime."""

        from afkbot.services.agent_loop.turn_context import TurnContextOverrides

        loop_settings = resolve_subagent_loop_settings(
            settings=self._settings,
            profile_id=profile_id,
            runtime_policy=self._runtime_policy,
        )
        self._ensure_llm_is_configured(settings=loop_settings)
        child_session_id = self._runtime_policy.build_child_session_id(task_id=task_id)
        runner = build_subagent_session_orchestrator(
            session_factory,
            loop_settings=loop_settings,
            runtime_policy=self._runtime_policy,
        )
        turn_task = asyncio.create_task(
            runner.run_turn(
                profile_id=profile_id,
                session_id=child_session_id,
                message=prompt,
                context_overrides=TurnContextOverrides(
                    prompt_overlay=self._runtime_policy.build_prompt_overlay(
                        subagent_name=subagent_name,
                        subagent_markdown=subagent_markdown,
                    ),
                    runtime_metadata={
                        "subagent_task": {
                            "task_id": task_id,
                            "name": subagent_name,
                            "parent_session_id": parent_session_id,
                        }
                    },
                ),
                source="subagent",
            ),
            name=f"subagent-child-turn:{task_id}",
        )
        try:
            result = await self._await_child_result(
                session_factory=session_factory,
                runner=runner,
                turn_task=turn_task,
                task_id=task_id,
                profile_id=profile_id,
                child_session_id=child_session_id,
            )
        finally:
            if not turn_task.done():
                turn_task.cancel()
                with suppress(asyncio.CancelledError):
                    await turn_task
        if result.envelope.action != "finalize":
            raise SubagentExecutionError(
                error_code="subagent_unexpected_action",
                reason=f"Subagent child run returned unsupported action: {result.envelope.action}",
            )
        async with session_scope(session_factory) as session:
            await self._raise_if_child_run_failed(session=session, run_id=result.run_id)
        return SubagentExecutionResult(
            output=result.envelope.message,
            child_session_id=child_session_id,
            child_run_id=result.run_id,
        )

    async def _await_child_result(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        runner: Any,
        turn_task: "asyncio.Task[Any]",
        task_id: str,
        profile_id: str,
        child_session_id: str,
    ) -> Any:
        while True:
            done, _pending = await asyncio.wait(
                {turn_task},
                timeout=_CANCEL_POLL_SEC,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                return await turn_task
            active, error_code, reason = await self._load_task_state(
                session_factory=session_factory,
                task_id=task_id,
            )
            if not active:
                await runner.cancel_active_turn(
                    profile_id=profile_id,
                    session_id=child_session_id,
                )
                try:
                    return await asyncio.wait_for(turn_task, timeout=_CANCEL_GRACE_SEC)
                except asyncio.TimeoutError:
                    turn_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await turn_task
                except asyncio.CancelledError:
                    pass
                raise SubagentExecutionError(
                    error_code=error_code or "subagent_cancelled",
                    reason=reason or "Subagent task was cancelled",
                )

    @staticmethod
    async def _load_task_state(
        *,
        session_factory: async_sessionmaker[AsyncSession],
        task_id: str,
    ) -> tuple[bool, str | None, str | None]:
        async with session_scope(session_factory) as session:
            row = await SubagentTaskRepository(session).get_task(task_id=task_id)
        if row is None:
            return False, None, None
        status = str(row.status or "").strip().lower()
        if status == "running":
            return True, None, None
        error_code = str(row.error_code or "").strip() or None
        reason = str(row.reason or "").strip() or None
        return False, error_code, reason

    @staticmethod
    def _ensure_llm_is_configured(*, settings: Settings) -> None:
        from afkbot.services.llm.provider_catalog import parse_provider
        from afkbot.services.llm.provider_settings import resolve_api_key

        provider_id = parse_provider(settings.llm_provider)
        if resolve_api_key(settings=settings, provider_id=provider_id):
            return
        raise SubagentExecutionError(
            error_code="subagent_llm_not_configured",
            reason=(
                "Subagent runtime requires configured provider credentials for the target profile."
            ),
        )

    @staticmethod
    async def _raise_if_child_run_failed(*, session: AsyncSession, run_id: int) -> None:
        """Raise deterministic error when child run finished through known LLM failure paths."""

        # Read-only inspection of child run events keeps fail-fast localized to subagent runtime.
        # We intentionally avoid changing the global TurnResult contract here.
        events = await RunlogRepository(session).list_run_events_since(
            run_id=run_id,
            after_event_id=0,
            limit=256,
        )
        for event in events:
            payload = _load_payload(event.payload_json)
            if event.event_type == "turn.finalize":
                blocked_reason = str(payload.get("blocked_reason") or "").strip()
                if blocked_reason:
                    raise SubagentExecutionError(
                        error_code=blocked_reason,
                        reason=f"Subagent child run finalized with blocked_reason={blocked_reason}",
                    )
                continue
            if event.event_type == "llm.call.timeout":
                error_code = str(payload.get("error_code") or "llm_timeout").strip()
                raise SubagentExecutionError(
                    error_code=error_code,
                    reason="Subagent child run timed out while waiting for the LLM provider.",
                )
            if event.event_type == "llm.call.error":
                error_code = str(payload.get("error_code") or "llm_provider_error").strip()
                reason = str(payload.get("reason") or "").strip() or (
                    "Subagent child run failed before the LLM provider completed."
                )
                raise SubagentExecutionError(error_code=error_code, reason=reason)
            if event.event_type == "llm.call.done":
                error_code = str(payload.get("error_code") or "").strip()
                if error_code:
                    reason = str(payload.get("reason") or "").strip() or (
                        f"Subagent child run completed with LLM error code: {error_code}"
                    )
                    raise SubagentExecutionError(error_code=error_code, reason=reason)


def _load_payload(payload_json: str) -> dict[str, object]:
    """Decode one runlog payload into dict form for child-run inspection."""

    try:
        raw = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}
