"""Pydantic contracts for Task Flow service responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskFlowMetadata(BaseModel):
    """Public metadata for one task flow container."""

    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    title: str
    description: str | None = None
    status: str
    created_by_type: str
    created_by_ref: str
    default_owner_type: str | None = None
    default_owner_ref: str | None = None
    labels: tuple[str, ...] = ()
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskMetadata(BaseModel):
    """Public metadata for one task item."""

    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    flow_id: str | None = None
    title: str
    prompt: str
    status: str
    priority: int
    due_at: datetime | None = None
    ready_at: datetime | None = None
    owner_type: str
    owner_ref: str
    reviewer_type: str | None = None
    reviewer_ref: str | None = None
    source_type: str
    source_ref: str | None = None
    created_by_type: str
    created_by_ref: str
    labels: tuple[str, ...] = ()
    depends_on_task_ids: tuple[str, ...] = ()
    requires_review: bool = False
    blocked_reason_code: str | None = None
    blocked_reason_text: str | None = None
    current_attempt: int
    last_session_id: str | None = None
    last_run_id: int | None = None
    last_error_code: str | None = None
    last_error_text: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskRunMetadata(BaseModel):
    """Public metadata for one Task Flow execution attempt."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: str
    attempt: int
    owner_type: str
    owner_ref: str
    execution_mode: str
    status: str
    session_id: str | None = None
    run_id: int | None = None
    worker_id: str | None = None
    summary: str | None = None
    error_code: str | None = None
    error_text: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StaleTaskClaimMetadata(BaseModel):
    """Public metadata for one expired in-flight task claim."""

    model_config = ConfigDict(extra="forbid")

    task: TaskMetadata
    claimed_by: str | None = None
    lease_until: datetime
    stale_for_sec: int = Field(ge=0)


class TaskMaintenanceSweepMetadata(BaseModel):
    """Public metadata for one bounded stale-claim maintenance sweep."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    profile_id: str | None = None
    limit: int = Field(ge=1)
    repaired_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    remaining: tuple[StaleTaskClaimMetadata, ...] = ()


class TaskEventMetadata(BaseModel):
    """Public metadata for one append-only task event."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: str
    task_run_id: int | None = None
    event_type: str
    actor_type: str | None = None
    actor_ref: str | None = None
    message: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class HumanTaskInboxEventMetadata(BaseModel):
    """Notification-ready event summary for one human inbox item."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: str
    task_title: str
    event_type: str
    actor_type: str | None = None
    actor_ref: str | None = None
    message: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class TaskCommentMetadata(BaseModel):
    """Public metadata for one append-only task comment."""

    model_config = ConfigDict(extra="forbid")

    id: int
    task_id: str
    task_run_id: int | None = None
    comment_type: str
    actor_type: str | None = None
    actor_ref: str | None = None
    message: str
    created_at: datetime


class TaskDependencyMetadata(BaseModel):
    """Public metadata for one dependency edge."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    depends_on_task_id: str
    satisfied_on_status: str
    created_at: datetime


class TaskBoardColumnMetadata(BaseModel):
    """One Task Flow board column with preview tasks."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    statuses: tuple[str, ...]
    count: int = Field(ge=0)
    tasks: tuple[TaskMetadata, ...] = ()


class TaskBoardMetadata(BaseModel):
    """Board/report view for one Task Flow backlog slice."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    profile_id: str
    flow_id: str | None = None
    owner_type: str | None = None
    owner_ref: str | None = None
    labels: tuple[str, ...] = ()
    limit_per_column: int = Field(ge=1)
    total_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    needs_review_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    columns: tuple[TaskBoardColumnMetadata, ...] = ()


class HumanTaskStartupSummary(BaseModel):
    """Summary of open human-owned tasks for chat startup notices."""

    model_config = ConfigDict(extra="forbid")

    owner_ref: str
    total_count: int = Field(ge=0)
    todo_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    tasks: tuple[TaskMetadata, ...] = ()


class HumanTaskInboxMetadata(BaseModel):
    """Notification-ready summary for one human Task Flow inbox."""

    model_config = ConfigDict(extra="forbid")

    owner_ref: str
    channel: str | None = None
    total_count: int = Field(ge=0)
    todo_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    overdue_count: int = Field(ge=0)
    unseen_event_count: int = Field(ge=0)
    tasks: tuple[TaskMetadata, ...] = ()
    recent_events: tuple[HumanTaskInboxEventMetadata, ...] = ()
