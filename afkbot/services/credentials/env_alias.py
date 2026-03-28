"""Helpers for deterministic credential environment-style aliases."""

from __future__ import annotations

import re

_ENV_ALIAS_PART_RE = re.compile(r"[^A-Za-z0-9]+")


def compute_env_key(*, app_name: str, profile_name: str, credential_slug: str) -> str:
    """Build deterministic `${ENV_KEY}` alias for credential placeholder lookup."""

    app_part = _normalize_env_alias_part(app_name)
    profile_part = _normalize_env_alias_part(profile_name)
    slug_part = _normalize_env_alias_part(credential_slug)
    return f"CRED_{app_part}_{profile_part}_{slug_part}"


def _normalize_env_alias_part(value: str) -> str:
    normalized = _ENV_ALIAS_PART_RE.sub("_", value.strip().upper()).strip("_")
    if normalized:
        return normalized
    return "X"
