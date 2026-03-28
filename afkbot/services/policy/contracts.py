"""Policy service contracts and typed exceptions."""

from __future__ import annotations


class PolicyEngineError(Exception):
    """Base class for policy-related runtime errors."""


class PolicyViolationError(PolicyEngineError):
    """Raised when profile policy denies one runtime operation."""

    def __init__(self, *, reason: str) -> None:
        super().__init__(reason)
        self.error_code = "profile_policy_violation"
        self.reason = reason


class ProfileFilesLockedError(PolicyEngineError):
    """Raised when profile file mutation lock is already held."""

    def __init__(self, *, profile_id: str) -> None:
        reason = f"Profile files are locked for profile: {profile_id}"
        super().__init__(reason)
        self.error_code = "profile_files_locked"
        self.reason = reason
        self.profile_id = profile_id
