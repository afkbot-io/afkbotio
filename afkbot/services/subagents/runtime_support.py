"""Internal helpers for persisted subagent lifecycle runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

from afkbot.models.subagent_task import SubagentTask
from afkbot.services.subagents.contracts import SubagentTaskStatus
from afkbot.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class SubagentTaskState:
    """Normalized task snapshot used by service methods and worker runtime."""

    task_id: str
    profile_id: str
    session_id: str
    subagent_name: str
    prompt: str
    timeout_sec: int
    status: SubagentTaskStatus
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    child_session_id: str | None
    child_run_id: int | None
    output: str | None
    error_code: str | None
    reason: str | None

    @classmethod
    def from_row(cls, row: SubagentTask) -> "SubagentTaskState":
        """Normalize repository row into stable UTC-aware runtime shape."""

        return cls(
            task_id=row.task_id,
            profile_id=row.profile_id,
            session_id=row.session_id,
            subagent_name=row.subagent_name,
            prompt=row.prompt,
            timeout_sec=row.timeout_sec,
            status=cast(SubagentTaskStatus, row.status),
            created_at=as_utc(row.created_at),
            started_at=as_utc(row.started_at),
            finished_at=as_utc(row.finished_at),
            child_session_id=row.child_session_id,
            child_run_id=row.child_run_id,
            output=row.output,
            error_code=row.error_code,
            reason=row.reason,
        )


def build_worker_env(settings: Settings) -> dict[str, str]:
    """Build clean worker environment for detached subagent process."""

    env = os.environ.copy()
    env["AFKBOT_ROOT_DIR"] = str(settings.root_dir)
    env["AFKBOT_DB_URL"] = settings.db_url
    env["AFKBOT_SUBAGENT_TIMEOUT_DEFAULT_SEC"] = str(settings.subagent_timeout_default_sec)
    env["AFKBOT_SUBAGENT_TIMEOUT_MAX_SEC"] = str(settings.subagent_timeout_max_sec)
    env["AFKBOT_SUBAGENT_TIMEOUT_GRACE_SEC"] = str(settings.subagent_timeout_grace_sec)
    env["AFKBOT_SUBAGENT_WAIT_DEFAULT_SEC"] = str(settings.subagent_wait_default_sec)
    env["AFKBOT_SUBAGENT_WAIT_MAX_SEC"] = str(settings.subagent_wait_max_sec)
    env["AFKBOT_SUBAGENT_TASK_TTL_SEC"] = str(settings.subagent_task_ttl_sec)
    return env


def spawn_worker(*, task_id: str, settings: Settings) -> None:
    """Start detached worker process for one persisted subagent task."""
    _spawn_worker_with_popen(task_id=task_id, settings=settings, popen=subprocess.Popen)


def _spawn_worker_with_popen(
    *,
    task_id: str,
    settings: Settings,
    popen: Callable[..., subprocess.Popen[Any]],
) -> None:
    """Start detached worker process using injectable Popen seam."""

    command = [
        sys.executable,
        "-m",
        "afkbot.workers.subagent_worker",
        "--task-id",
        task_id,
    ]
    popen(  # noqa: S603
        command,
        cwd=str(PROJECT_ROOT),
        env=build_worker_env(settings),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def resolve_timeout(*, timeout_sec: int | None, settings: Settings) -> int:
    """Clamp requested task timeout into configured service bounds."""

    if timeout_sec is None:
        return settings.subagent_timeout_default_sec
    return max(1, min(timeout_sec, settings.subagent_timeout_max_sec))


def resolve_wait_timeout(*, timeout_sec: int | None, settings: Settings) -> float:
    """Clamp wait timeout into configured polling bounds."""

    if timeout_sec is None:
        return float(settings.subagent_wait_default_sec)
    return float(max(1, min(timeout_sec, settings.subagent_wait_max_sec)))


def ensure_owner_access(
    *,
    task_profile_id: str,
    task_session_id: str,
    profile_id: str | None,
    session_id: str | None,
) -> None:
    """Ensure caller can access persisted subagent task state."""

    if profile_id is not None and task_profile_id != profile_id:
        raise PermissionError("subagent_task_forbidden")
    if session_id is not None and task_session_id != session_id:
        raise PermissionError("subagent_task_forbidden")


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize optional datetime to UTC-aware value."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_task_overdue(
    row: SubagentTaskState | SubagentTask,
    *,
    settings: Settings,
) -> bool:
    """Return whether running task exceeded timeout plus grace window."""

    start = as_utc(row.started_at) or as_utc(row.created_at)
    if start is None:
        return False
    grace = max(0, int(settings.subagent_timeout_grace_sec))
    deadline = start + timedelta(seconds=max(1, int(row.timeout_sec)) + grace)
    return datetime.now(timezone.utc) >= deadline
