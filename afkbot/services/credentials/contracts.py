"""Pydantic contracts for credential metadata responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CredentialBindingMetadata(BaseModel):
    """Public metadata for one credential binding without plaintext secret."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_id: str
    integration_name: str
    credential_profile_key: str
    tool_name: str | None
    credential_name: str
    key_version: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CredentialProfileMetadata(BaseModel):
    """Public metadata for one credential profile."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_id: str
    integration_name: str
    profile_key: str
    display_name: str
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
