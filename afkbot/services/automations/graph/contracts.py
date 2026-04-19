"""Public contracts for automation graph persistence and inspection."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AutomationGraphNodeKind = Literal["builtin", "code", "ai", "agent", "task", "action"]
AutomationExecutionMode = Literal["prompt", "graph"]
AutomationGraphFallbackMode = Literal[
    "fail_closed",
    "resume_with_ai",
    "resume_with_ai_if_safe",
]
AutomationGraphRunStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "fallback_succeeded",
    "fallback_failed",
]
AutomationGraphNodeStatus = Literal["pending", "running", "succeeded", "failed", "skipped"]


class AutomationGraphVersionSpec(BaseModel):
    """Inline versioned artifact spec for one graph node."""

    model_config = ConfigDict(extra="forbid")

    runtime: str
    version_label: str | None = None
    source_code: str
    config_schema: dict[str, object] | None = None
    input_schema: dict[str, object] | None = None
    output_schema: dict[str, object] | None = None
    manifest: dict[str, object] = Field(default_factory=dict)
    tests: dict[str, object] | None = None


class AutomationGraphNodeSpec(BaseModel):
    """Node placement spec inside one graph flow."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    node_kind: AutomationGraphNodeKind
    node_type: str = Field(min_length=1)
    config: dict[str, object] = Field(default_factory=dict)
    version: AutomationGraphVersionSpec | None = None


class AutomationGraphEdgeSpec(BaseModel):
    """Directed edge spec between graph nodes."""

    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    source_port: str = "default"
    target_port: str = "default"


class AutomationGraphSpec(BaseModel):
    """Top-level graph write spec for one automation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    nodes: list[AutomationGraphNodeSpec] = Field(default_factory=list)
    edges: list[AutomationGraphEdgeSpec] = Field(default_factory=list)


class AutomationGraphNodeMetadata(BaseModel):
    """Read-model for one persisted graph node."""

    model_config = ConfigDict(extra="forbid")

    id: int
    key: str
    name: str
    node_kind: AutomationGraphNodeKind
    node_type: str
    config: dict[str, object] = Field(default_factory=dict)
    node_version_id: int | None = None


class AutomationGraphEdgeMetadata(BaseModel):
    """Read-model for one persisted graph edge."""

    model_config = ConfigDict(extra="forbid")

    id: int
    source_key: str
    target_key: str
    source_port: str
    target_port: str


class AutomationGraphMetadata(BaseModel):
    """Terminal-first graph snapshot for one automation."""

    model_config = ConfigDict(extra="forbid")

    flow_id: int
    automation_id: int
    execution_mode: AutomationExecutionMode
    graph_fallback_mode: AutomationGraphFallbackMode
    name: str
    version: int
    status: str
    nodes: list[AutomationGraphNodeMetadata]
    edges: list[AutomationGraphEdgeMetadata]


class AutomationGraphValidationReport(BaseModel):
    """Validation outcome for one graph snapshot."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)


class AutomationGraphRunMetadata(BaseModel):
    """Read-model for one automation graph run."""

    model_config = ConfigDict(extra="forbid")

    id: int
    automation_id: int
    flow_id: int | None
    profile_id: str
    trigger_type: Literal["cron", "webhook"]
    status: AutomationGraphRunStatus
    parent_session_id: str | None
    event_hash: str | None
    fallback_status: str | None = None
    error_code: str | None = None
    reason: str | None = None
    final_output: dict[str, object] | None = None
    started_at: datetime
    completed_at: datetime | None = None


class AutomationGraphNodeEffectMetadata(BaseModel):
    """Structured side-effect ledger item for one node run."""

    model_config = ConfigDict(extra="forbid")

    effect_kind: str
    safety_class: str
    committed: bool
    idempotency_key: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AutomationGraphNodeRunMetadata(BaseModel):
    """Read-model for one per-node execution record."""

    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: int
    node_key: str
    status: AutomationGraphNodeStatus
    attempt: int
    execution_index: int | None = None
    selected_ports: list[str] = Field(default_factory=list)
    effects: list[AutomationGraphNodeEffectMetadata] = Field(default_factory=list)
    input: dict[str, object] = Field(default_factory=dict)
    output: dict[str, object] | None = None
    error_code: str | None = None
    reason: str | None = None
    child_task_id: str | None = None
    child_session_id: str | None = None
    child_run_id: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AutomationGraphFallbackTraceMetadata(BaseModel):
    """Synthetic ordered fallback event exposed alongside node trace data."""

    model_config = ConfigDict(extra="forbid")

    execution_index: int
    status: str
    error_code: str | None = None
    reason: str | None = None
    output: dict[str, object] | None = None


class AutomationGraphTraceMetadata(BaseModel):
    """Run + ordered node trace payload for terminal inspection."""

    model_config = ConfigDict(extra="forbid")

    run: AutomationGraphRunMetadata
    nodes: list[AutomationGraphNodeRunMetadata]
    fallback: AutomationGraphFallbackTraceMetadata | None = None
