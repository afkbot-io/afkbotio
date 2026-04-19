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


class TaskSessionActivityMetadata(BaseModel):
    """Live session/dialog state attached to one task when work is in progress."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    session_profile_id: str
    dialog_active: bool = False
    queued_turn_count: int = Field(default=0, ge=0)
    running_turn_count: int = Field(default=0, ge=0)
    latest_activity_at: datetime | None = None


class TaskBlockStateMetadata(BaseModel):
    """Derived blocker state for operator/UI/runtime convenience."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    waiting_for_human: bool = False
    waiting_for_dependency: bool = False
    retry_scheduled: bool = False
    ready_at: datetime | None = None
    depends_on_task_ids: tuple[str, ...] = ()


class TaskAttachmentCreate(BaseModel):
    """Validated binary attachment payload accepted by Task Flow APIs."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    content_type: str | None = Field(default=None, max_length=255)
    kind: str = Field(default="file", min_length=1, max_length=32)


class TaskAttachmentMetadata(BaseModel):
    """Metadata for one persisted task attachment."""

    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    profile_id: str
    name: str
    content_type: str | None = None
    kind: str
    byte_size: int = Field(ge=0)
    sha256: str
    created_by_type: str
    created_by_ref: str
    created_at: datetime
    updated_at: datetime


class TaskAttachmentContent(BaseModel):
    """Attachment metadata plus binary content for API download paths."""

    model_config = ConfigDict(extra="forbid")

    attachment: TaskAttachmentMetadata
    content_bytes: bytes


class TaskMetadata(BaseModel):
    """Public metadata for one task item."""

    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    flow_id: str | None = None
    title: str
    description: str
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
    block_state: TaskBlockStateMetadata | None = None
    current_attempt: int
    last_session_id: str | None = None
    last_session_profile_id: str | None = None
    active_session: TaskSessionActivityMetadata | None = None
    last_run_id: int | None = None
    last_error_code: str | None = None
    last_error_text: str | None = None
    attachment_count: int = Field(default=0, ge=0)
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


class TaskDelegationMetadata(BaseModel):
    """Structured result for delegating one task to another AI owner."""

    model_config = ConfigDict(extra="forbid")

    source_task: TaskMetadata
    delegated_task: TaskMetadata
    dependency: TaskDependencyMetadata | None = None


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
