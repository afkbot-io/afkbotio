"""Shared profile identifier validation helpers."""

from __future__ import annotations

import re

_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class InvalidProfileIdError(ValueError):
    """Raised when a profile identifier violates the safety contract."""


def validate_profile_id(profile_id: str) -> str:
    """Validate and return profile id constrained to safe filesystem segment."""

    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise InvalidProfileIdError(f"Invalid profile id: {profile_id}")
    return profile_id
