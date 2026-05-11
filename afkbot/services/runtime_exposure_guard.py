"""Fail-closed runtime exposure policy for local and managed starts."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from urllib.parse import urlparse

from afkbot.services.ui_auth.policy import ui_auth_is_configured
from afkbot.settings import Settings


class RuntimeExposureGuardError(ValueError):
    """Raised when runtime exposure would open an unsafe surface."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RuntimeExposureDecision:
    """Resolved exposure posture for one runtime start."""

    host: str
    public_bind: bool
    public_runtime_url: bool
    public_chat_api_url: bool

    @property
    def exposed(self) -> bool:
        return self.public_bind or self.public_runtime_url or self.public_chat_api_url


def validate_runtime_exposure(
    *,
    settings: Settings,
    host: str,
    context: str,
) -> RuntimeExposureDecision:
    """Validate runtime exposure settings before binding or persisting them."""

    decision = RuntimeExposureDecision(
        host=host,
        public_bind=not is_loopback_bind_host(host),
        public_runtime_url=_is_public_http_surface(settings.public_runtime_url),
        public_chat_api_url=_is_public_http_surface(settings.public_chat_api_url),
    )
    if not decision.exposed:
        return decision

    if settings.runtime_public_bind_policy != "auth_required":
        raise RuntimeExposureGuardError(
            error_code="runtime_public_exposure_blocked",
            reason=(
                f"{context} would expose AFKBOT outside loopback. "
                "Set AFKBOT_RUNTIME_PUBLIC_BIND_POLICY=auth_required only when the runtime is "
                "behind the intended operator auth and network boundary."
            ),
        )
    if not ui_auth_is_configured(settings):
        raise RuntimeExposureGuardError(
            error_code="runtime_public_exposure_auth_required",
            reason=(
                f"{context} would expose AFKBOT outside loopback, but UI/plugin auth is not fully "
                "configured. Configure AFKBOT_UI_AUTH_MODE=password, username, password hash, "
                "and cookie key before public exposure."
            ),
        )
    if not settings.plugin_api_auth_required:
        raise RuntimeExposureGuardError(
            error_code="runtime_plugin_api_auth_required",
            reason=(
                f"{context} would expose AFKBOT outside loopback while plugin API auth is disabled. "
                "Keep AFKBOT_PLUGIN_API_AUTH_REQUIRED=1 for public or managed runtimes."
            ),
        )
    return decision


def is_loopback_bind_host(host: str) -> bool:
    """Return whether a bind host is limited to the local machine."""

    normalized = str(host or "").strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    if normalized in {"", "0.0.0.0", "::", "[::]"}:
        return False
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback


def _is_public_http_surface(raw_url: str | None) -> bool:
    normalized = str(raw_url or "").strip()
    if not normalized:
        return False
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    return not is_loopback_bind_host(host)
