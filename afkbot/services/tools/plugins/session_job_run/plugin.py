"""Unified session job fan-out tool."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError

from afkbot.services.policy import PolicyViolationError
from afkbot.services.subagents import get_subagent_service
from afkbot.services.subagents.contracts import SubagentResultResponse, SubagentRunAccepted
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters, build_tool_parameters
from afkbot.services.tools.plugins.bash_exec.plugin import BashExecTool
from afkbot.settings import Settings

_MAX_JOB_BATCH_SIZE = 6
_WAIT_SLICE_SEC = 30


class SessionJobSpec(BaseModel):
    """One parallel job in a session job group."""

    kind: Literal["subagent", "bash"]
    prompt: str | None = Field(default=None, max_length=20_000)
    subagent_name: str | None = Field(default=None)
    cmd: str | None = Field(default=None, max_length=8000)
    cwd: str = Field(default=".", min_length=1, max_length=4096)
    env: dict[str, str] = Field(default_factory=dict)
    shell: str | None = Field(default=None, min_length=1, max_length=256)
    login: bool = False
    timeout_sec: int | None = Field(default=None, ge=1)


class SessionJobRunParams(ToolParameters):
    """Parameters for session.job.run."""

    jobs: list[SessionJobSpec] = Field(min_length=1, max_length=_MAX_JOB_BATCH_SIZE)


class SessionJobRunTool(ToolBase):
    """Run independent session jobs concurrently and return ordered results."""

    name = "session.job.run"
    description = (
        "Run independent session jobs concurrently and return ordered results. "
        "Supported kinds: subagent and bash. Prefer this tool over separate `bash.exec` "
        "or `subagent.run` calls when two or more independent jobs can start immediately "
        "and every result is needed before continuing. Use only when jobs do not depend "
        "on one another."
    )
    parameters_model = SessionJobRunParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bash = BashExecTool(settings)

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        payload: dict[str, object] = dict(raw_params)
        nested_timeout = self._max_nested_timeout(payload)
        if nested_timeout is not None:
            requested_timeout = self._coerce_timeout(payload.get("timeout_sec"))
            payload["timeout_sec"] = max(
                default_timeout_sec,
                requested_timeout or 0,
                nested_timeout,
            )
        return build_tool_parameters(
            self.parameters_model,
            payload,
            default_timeout_sec=default_timeout_sec,
            max_timeout_sec=max_timeout_sec,
        )

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SessionJobRunParams.model_validate(params.model_dump())
        if ctx.actor == "subagent":
            return ToolResult.error(
                error_code="session_job_recursive_spawn_forbidden",
                reason="Subagent cannot spawn session jobs",
            )

        subagent_service = (
            get_subagent_service(self._settings)
            if any(job.kind == "subagent" for job in payload.jobs)
            else None
        )
        accepted_task_ids: set[str] = set()
        try:
            results = await asyncio.gather(
                *(
                    self._run_one(
                        subagent_service=subagent_service,
                        ctx=ctx,
                        job=job,
                        index=index,
                        accepted_task_ids=accepted_task_ids,
                    )
                    for index, job in enumerate(payload.jobs)
                )
            )
        except asyncio.CancelledError:
            await self._cancel_accepted_subagents(
                service=subagent_service,
                ctx=ctx,
                task_ids=accepted_task_ids,
            )
            raise
        failed = sum(1 for item in results if not bool(item.get("ok")))
        return ToolResult(
            ok=True,
            payload={
                "total": len(results),
                "completed": len(results) - failed,
                "failed": failed,
                "results": results,
            },
        )

    async def _run_one(
        self,
        *,
        subagent_service: Any | None,
        ctx: ToolContext,
        job: SessionJobSpec,
        index: int,
        accepted_task_ids: set[str],
    ) -> dict[str, object]:
        if job.kind == "subagent":
            if subagent_service is None:
                return self._error_result(
                    index=index,
                    kind="subagent",
                    error_code="subagent_service_unavailable",
                    reason="Subagent service is unavailable",
                )
            return await self._run_subagent_job(
                service=subagent_service,
                ctx=ctx,
                job=job,
                index=index,
                accepted_task_ids=accepted_task_ids,
            )
        return await self._run_bash_job(ctx=ctx, job=job, index=index)

    async def _run_bash_job(
        self,
        *,
        ctx: ToolContext,
        job: SessionJobSpec,
        index: int,
    ) -> dict[str, object]:
        if not str(job.cmd or "").strip():
            return self._error_result(
                index=index,
                kind="bash",
                error_code="tool_params_invalid",
                reason="bash job requires cmd",
            )
        try:
            params = self._bash.parse_params(
                {
                    "cmd": job.cmd,
                    "cwd": job.cwd,
                    "env": job.env,
                    "shell": job.shell,
                    "login": job.login,
                    **({"timeout_sec": job.timeout_sec} if job.timeout_sec is not None else {}),
                    "profile_id": ctx.profile_id,
                    "profile_key": ctx.profile_id,
                },
                default_timeout_sec=self._settings.tool_timeout_default_sec,
                max_timeout_sec=self._settings.tool_timeout_max_sec,
            )
        except (ValidationError, ValueError) as exc:
            return self._error_result(
                index=index,
                kind="bash",
                error_code="tool_params_invalid",
                reason=str(exc),
            )
        result = await self._bash.execute(replace(ctx, progress_callback=None), params)
        return {
            "index": index,
            "kind": "bash",
            "ok": result.ok,
            "status": "completed" if result.ok else "failed",
            "payload": result.payload,
            "error_code": result.error_code,
            "reason": result.reason,
            "metadata": result.metadata,
        }

    async def _run_subagent_job(
        self,
        *,
        service: Any,
        ctx: ToolContext,
        job: SessionJobSpec,
        index: int,
        accepted_task_ids: set[str],
    ) -> dict[str, object]:
        prompt = str(job.prompt or "").strip()
        if not prompt:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code="tool_params_invalid",
                reason="subagent job requires prompt",
            )
        try:
            accepted = await service.run(
                ctx=ctx,
                prompt=prompt,
                subagent_name=job.subagent_name,
                timeout_sec=job.timeout_sec,
            )
            accepted_task_ids.add(accepted.task_id)
            result = await self._await_subagent_result(
                service=service,
                ctx=ctx,
                accepted=accepted,
            )
        except FileNotFoundError:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code="subagent_not_found",
                reason=f"Subagent not found: {job.subagent_name}",
            )
        except PermissionError as exc:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code="session_job_recursive_spawn_forbidden",
                reason=str(exc),
            )
        except PolicyViolationError as exc:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code=exc.error_code,
                reason=exc.reason,
            )
        except ValueError as exc:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code=_subagent_value_error_code(exc),
                reason=str(exc),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                index=index,
                kind="subagent",
                error_code="subagent_run_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )
        return {
            "index": index,
            "kind": "subagent",
            "ok": result.status == "completed",
            "task_id": accepted.task_id,
            "subagent_name": accepted.subagent_name,
            "status": result.status,
            "child_session_id": result.child_session_id,
            "child_run_id": result.child_run_id,
            "output": result.output,
            "error_code": result.error_code,
            "reason": result.reason,
        }

    async def _await_subagent_result(
        self,
        *,
        service: Any,
        ctx: ToolContext,
        accepted: SubagentRunAccepted,
    ) -> SubagentResultResponse:
        deadline = asyncio.get_running_loop().time() + float(max(1, accepted.timeout_sec))
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self._cancel_accepted_subagents(
                    service=service,
                    ctx=ctx,
                    task_ids={accepted.task_id},
                )
                return SubagentResultResponse(
                    task_id=accepted.task_id,
                    status="timeout",
                    error_code="subagent_timeout",
                    reason=f"Subagent timed out after {accepted.timeout_sec} seconds",
                )
            wait_result = await service.wait(
                task_id=accepted.task_id,
                timeout_sec=max(1, min(_WAIT_SLICE_SEC, int(remaining))),
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
            )
            if wait_result.done:
                return cast(
                    SubagentResultResponse,
                    await service.result(
                        task_id=accepted.task_id,
                        profile_id=ctx.profile_id,
                        session_id=ctx.session_id,
                    ),
                )

    async def _cancel_accepted_subagents(
        self,
        *,
        service: Any,
        ctx: ToolContext,
        task_ids: set[str],
    ) -> None:
        cancel = getattr(service, "cancel", None)
        if not callable(cancel) or not task_ids:
            return
        await asyncio.gather(
            *(
                cancel(
                    task_id=task_id,
                    profile_id=ctx.profile_id,
                    session_id=ctx.session_id,
                )
                for task_id in sorted(task_ids)
            ),
            return_exceptions=True,
        )

    @staticmethod
    def _error_result(
        *,
        index: int,
        kind: str,
        error_code: str,
        reason: str,
    ) -> dict[str, object]:
        return {
            "index": index,
            "kind": kind,
            "ok": False,
            "status": "failed",
            "payload": None,
            "output": None,
            "error_code": error_code,
            "reason": reason,
        }

    @staticmethod
    def _max_nested_timeout(raw_params: Mapping[str, object]) -> int | None:
        jobs = raw_params.get("jobs")
        if not isinstance(jobs, list):
            return None
        values: list[int] = []
        for job in jobs:
            if not isinstance(job, Mapping):
                continue
            timeout = SessionJobRunTool._coerce_timeout(job.get("timeout_sec"))
            if timeout is not None:
                values.append(timeout)
        return max(values) if values else None

    @staticmethod
    def _coerce_timeout(value: object) -> int | None:
        if value is None:
            return None
        try:
            timeout = int(str(value))
        except (TypeError, ValueError):
            return None
        return timeout if timeout >= 1 else None


def create_tool(settings: Settings) -> ToolBase:
    """Create session.job.run tool instance."""

    return SessionJobRunTool(settings=settings)


def _subagent_value_error_code(exc: ValueError) -> str:
    reason = str(exc).strip()
    if reason.startswith("Invalid subagent name:"):
        return "invalid_subagent_name"
    return "tool_params_invalid"
