"""Pydantic contracts for automation service responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

WEBHOOK_INGRESS_PATH = "/v1/automations"


class AutomationCronMetadata(BaseModel):
    """Cron trigger metadata attached to one automation."""

    model_config = ConfigDict(extra="forbid")

    cron_expr: str
    timezone: str
    next_run_at: datetime | None
    last_run_at: datetime | None


class AutomationWebhookMetadata(BaseModel):
    """Webhook trigger metadata attached to one automation."""

    model_config = ConfigDict(extra="forbid")

    webhook_token: str | None = None
    webhook_path: str | None = None
    webhook_url: str | None = None
    webhook_token_masked: str
    last_execution_status: Literal["idle", "received", "running", "succeeded", "failed"]
    last_received_at: datetime | None
    last_succeeded_at: datetime | None = None
    last_failed_at: datetime | None = None
    last_error: str | None = None
    last_session_id: str | None = None
    last_event_hash: str | None = None


class AutomationMetadata(BaseModel):
    """Public metadata for one automation descriptor."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_id: str
    name: str
    prompt: str
    trigger_type: Literal["cron", "webhook"]
    status: Literal["active", "paused", "deleted"]
    created_at: datetime
    updated_at: datetime
    cron: AutomationCronMetadata | None = None
    webhook: AutomationWebhookMetadata | None = None


class AutomationWebhookTriggerResult(BaseModel):
    """Result returned after webhook trigger processing."""

    model_config = ConfigDict(extra="forbid")

    automation_id: int
    profile_id: str
    session_id: str
    payload: dict[str, Any]
    deduplicated: bool = False


class AutomationCronTickResult(BaseModel):
    """Cron tick summary result."""

    model_config = ConfigDict(extra="forbid")

    triggered_ids: list[int]
    failed_ids: list[int]
