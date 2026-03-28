"""Contracts and integration specs for health/doctor checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.services.channel_routing.contracts import ChannelRoutingDiagnostics
from afkbot.services.channels.contracts import ChannelDeliveryDiagnostics

IntegrationStatus = Literal["ok", "skip", "fail"]
MatrixMode = Literal["config", "probe"]
IntegrationKind = Literal["tool", "llm"]


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Structured health-check report."""

    bootstrap_ok: bool
    db_ok: bool

    @property
    def ok(self) -> bool:
        """Return overall status for all mandatory checks."""

        return self.bootstrap_ok and self.db_ok


class HealthServiceError(ValueError):
    """Structured health-check error surfaced by doctor/API callers."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class IntegrationCheck:
    """Single integration matrix entry."""

    integration: str
    status: IntegrationStatus
    mode: MatrixMode
    reason: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrationMatrixReport:
    """Integration readiness/probe report."""

    checks: tuple[IntegrationCheck, ...]

    @property
    def ok(self) -> bool:
        """Return true when no integration check has fail status."""

        return all(item.status != "fail" for item in self.checks)


@dataclass(frozen=True, slots=True)
class DoctorRoutingReport:
    """Operator-facing routing diagnostics snapshot rendered by doctor."""

    diagnostics: ChannelRoutingDiagnostics
    fallback_transports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DoctorDeliveryReport:
    """Operator-facing outbound delivery diagnostics snapshot."""

    diagnostics: ChannelDeliveryDiagnostics


@dataclass(frozen=True, slots=True)
class TelegramPollingEndpointReport:
    """Operator-facing Telegram Bot API polling endpoint status."""

    endpoint_id: str
    enabled: bool
    profile_id: str
    credential_profile_key: str
    account_id: str
    profile_valid: bool
    profile_exists: bool
    token_configured: bool
    binding_count: int
    state_path: str
    state_present: bool


@dataclass(frozen=True, slots=True)
class TelethonUserEndpointReport:
    """Operator-facing Telethon userbot endpoint status."""

    endpoint_id: str
    enabled: bool
    profile_id: str
    credential_profile_key: str
    account_id: str
    profile_valid: bool
    profile_exists: bool
    api_id_configured: bool
    api_hash_configured: bool
    phone_configured: bool
    session_string_configured: bool
    policy_allows_runtime: bool
    binding_count: int
    state_path: str
    state_present: bool


@dataclass(frozen=True, slots=True)
class DoctorChannelsReport:
    """Operator-facing channel health snapshot."""

    telegram_polling: tuple[TelegramPollingEndpointReport, ...]
    telethon_userbot: tuple[TelethonUserEndpointReport, ...] = ()


@dataclass(frozen=True, slots=True)
class IntegrationSpec:
    """Static health-check metadata for one integration."""

    integration: str
    required_credentials: tuple[str, ...]
    kind: IntegrationKind = "tool"
    tool_name: str | None = None
    requires_credentials_service: bool = False
    probe_supported: bool = True


INTEGRATIONS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        integration="llm",
        kind="llm",
        required_credentials=(),
    ),
    IntegrationSpec(
        integration="http",
        tool_name="http.request",
        required_credentials=(),
    ),
    IntegrationSpec(
        integration="web.search",
        tool_name="web.search",
        required_credentials=(),
        probe_supported=False,
    ),
    IntegrationSpec(
        integration="web.fetch",
        tool_name="web.fetch",
        required_credentials=(),
        probe_supported=False,
    ),
    IntegrationSpec(
        integration="browser.control",
        tool_name="browser.control",
        required_credentials=(),
        probe_supported=False,
    ),
    IntegrationSpec(
        integration="app.list",
        tool_name="app.list",
        required_credentials=(),
        probe_supported=False,
    ),
    IntegrationSpec(
        integration="credentials.request",
        tool_name="credentials.request",
        required_credentials=(),
        requires_credentials_service=True,
        probe_supported=False,
    ),
    IntegrationSpec(
        integration="telegram",
        tool_name="app.run",
        required_credentials=("telegram_token", "telegram_chat_id"),
        requires_credentials_service=True,
    ),
    IntegrationSpec(
        integration="imap",
        tool_name="app.run",
        required_credentials=("imap_host", "imap_port", "imap_username", "imap_password"),
        requires_credentials_service=True,
    ),
    IntegrationSpec(
        integration="smtp",
        tool_name="app.run",
        required_credentials=(
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_from_email",
        ),
        requires_credentials_service=True,
    ),
)
