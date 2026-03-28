"""Contracts and deterministic errors for desktop connect flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class ConnectClientMetadata:
    """Optional client fingerprint metadata attached at connect claim time."""

    platform: str | None = None
    app_version: str | None = None

    def serialize(self) -> dict[str, str]:
        """Return canonical JSON-friendly payload without empty fields."""

        payload = {
            "platform": (self.platform or "").strip(),
            "app_version": (self.app_version or "").strip(),
        }
        return {
            key: value
            for key, value in payload.items()
            if value
        }


@dataclass(slots=True, frozen=True)
class ConnectIssueResult:
    """Connect URL issued by CLI for one desktop pairing flow."""

    connect_url: str
    expires_at: datetime
    profile_id: str
    session_id: str
    base_url: str
    claim_pin: str | None = None


@dataclass(slots=True, frozen=True)
class ConnectClaimResult:
    """Claim response payload returned to desktop app."""

    access_token: str
    refresh_token: str
    session_proof: str
    expires_in_sec: int
    base_url: str
    profile_id: str
    session_id: str


@dataclass(slots=True, frozen=True)
class ConnectRefreshResult:
    """Access-token refresh response payload."""

    access_token: str
    refresh_token: str
    expires_in_sec: int
    session_id: str


@dataclass(slots=True, frozen=True)
class ConnectAccessTokenContext:
    """Resolved scope for one validated access token."""

    profile_id: str
    session_id: str
    base_url: str
    expires_at: datetime
    allow_diagnostics: bool = False
    runtime_metadata: dict[str, object] | None = None
    prompt_overlay: str | None = None


class ConnectServiceError(RuntimeError):
    """Deterministic connect lifecycle service error."""

    def __init__(self, *, error_code: str, reason: str, retry_after_sec: int | None = None) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.retry_after_sec = retry_after_sec
