"""Contracts for app runtime dispatch used by `app.run` tool."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppRuntimeContext:
    """Execution context for one app action call."""

    profile_id: str
    session_id: str
    run_id: int
    credential_profile_key: str | None
    timeout_sec: int
