"""GitHub Copilot OAuth token exchange helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from urllib.parse import urlparse

import httpx

COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
DEFAULT_COPILOT_API_BASE_URL = "https://api.individual.githubcopilot.com"
COPILOT_EDITOR_VERSION = "vscode/1.96.2"
COPILOT_USER_AGENT = "GitHubCopilotChat/0.26.7"
COPILOT_GITHUB_API_VERSION = "2025-04-01"
_COPILOT_TOKEN_SAFETY_WINDOW_MS = 5 * 60 * 1000

_COPILOT_TOKEN_CACHE: dict[str, "ResolvedCopilotApiToken"] = {}


@dataclass(frozen=True, slots=True)
class ResolvedCopilotApiToken:
    """Exchanged Copilot API token derived from a GitHub OAuth access token."""

    token: str
    expires_at_ms: int
    base_url: str


def build_copilot_ide_headers(*, include_api_version: bool = False) -> dict[str, str]:
    """Return the request headers expected by GitHub Copilot token/runtime endpoints."""

    headers = {
        "Editor-Version": COPILOT_EDITOR_VERSION,
        "User-Agent": COPILOT_USER_AGENT,
    }
    if include_api_version:
        headers["X-Github-Api-Version"] = COPILOT_GITHUB_API_VERSION
    return headers


def derive_copilot_api_base_url_from_token(token: str) -> str | None:
    """Derive Copilot API base URL from exchanged token metadata when available."""

    match = re.search(r"(?:^|;)\s*proxy-ep=([^;\s]+)", token.strip(), flags=re.IGNORECASE)
    if match is None:
        return None
    proxy_ep = match.group(1).strip()
    if not proxy_ep:
        return None
    if not proxy_ep.lower().startswith(("http://", "https://")):
        proxy_ep = f"https://{proxy_ep}"
    try:
        parsed = urlparse(proxy_ep)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return None
    api_host = re.sub(r"^proxy\.", "api.", host, flags=re.IGNORECASE)
    return f"https://{api_host}"


def resolve_copilot_api_token(
    *,
    github_token: str,
    proxy_url: str | None = None,
    timeout_sec: float = 10.0,
) -> ResolvedCopilotApiToken:
    """Exchange one GitHub OAuth token into a short-lived Copilot API token."""

    normalized = github_token.strip()
    if not normalized:
        raise ValueError("GitHub OAuth token is empty")

    cached = _COPILOT_TOKEN_CACHE.get(normalized)
    now_ms = int(time.time() * 1000)
    if cached is not None and (cached.expires_at_ms - now_ms) > _COPILOT_TOKEN_SAFETY_WINDOW_MS:
        return cached

    with httpx.Client(timeout=timeout_sec, proxy=proxy_url, trust_env=False) as client:
        response = client.get(
            COPILOT_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {normalized}",
                **build_copilot_ide_headers(include_api_version=True),
            },
        )
        response.raise_for_status()
        payload = response.json()

    token, expires_at_ms = _parse_copilot_token_payload(payload)
    base_url = derive_copilot_api_base_url_from_token(token) or DEFAULT_COPILOT_API_BASE_URL
    resolved = ResolvedCopilotApiToken(
        token=token,
        expires_at_ms=expires_at_ms,
        base_url=base_url,
    )
    _COPILOT_TOKEN_CACHE[normalized] = resolved
    return resolved


def reset_copilot_api_token_cache() -> None:
    """Reset in-memory Copilot exchange cache (tests/debug only)."""

    _COPILOT_TOKEN_CACHE.clear()


def _parse_copilot_token_payload(payload: object) -> tuple[str, int]:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected response from GitHub Copilot token endpoint")

    token_raw = payload.get("token")
    if not isinstance(token_raw, str) or not token_raw.strip():
        raise ValueError("Copilot token response is missing token")

    expires_raw = payload.get("expires_at")
    expires_at_ms = _normalize_expires_to_millis(expires_raw)
    return token_raw.strip(), expires_at_ms


def _normalize_expires_to_millis(value: object) -> int:
    if isinstance(value, int | float):
        expires = int(value)
    elif isinstance(value, str) and value.strip():
        expires = int(value.strip())
    else:
        raise ValueError("Copilot token response is missing expires_at")

    # GitHub generally returns epoch seconds; keep compatibility with ms values.
    if expires < 100_000_000_000:
        return expires * 1000
    return expires
