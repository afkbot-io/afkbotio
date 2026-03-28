"""Model package exports and metadata registration helpers."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

_MODEL_EXPORT_MODULES = {
    "Automation": "automation",
    "AutomationTriggerCron": "automation_trigger_cron",
    "AutomationTriggerWebhook": "automation_trigger_webhook",
    "AutomationWebhookProcessedEvent": "automation_webhook_processed_event",
    "ChatSession": "chat_session",
    "ChatSessionCompaction": "chat_session_compaction",
    "ChatTurn": "chat_turn",
    "ChatTurnIdempotency": "chat_turn_idempotency",
    "ChatTurnIdempotencyClaim": "chat_turn_idempotency",
    "ChannelBinding": "channel_binding",
    "ChannelEndpoint": "channel_endpoint",
    "ChannelIngressEvent": "channel_ingress_event",
    "ChannelIngressPendingEvent": "channel_ingress_pending_event",
    "ConnectAccessToken": "connect_access_token",
    "ConnectClaimToken": "connect_claim_token",
    "ConnectSessionToken": "connect_session_token",
    "CredentialProfile": "credential_profile",
    "MemoryItem": "memory_item",
    "PendingResumeEnvelope": "pending_resume_envelope",
    "PendingSecureRequest": "pending_secure_request",
    "Profile": "profile",
    "ProfilePolicy": "profile_policy",
    "Run": "run",
    "RunlogEvent": "runlog_event",
    "Secret": "secret",
    "SubagentTask": "subagent_task",
    "ToolCredentialBinding": "tool_credential_binding",
}


def load_all_models() -> None:
    """Import every ORM model module so metadata registration never depends on drifted lists."""

    package_dir = Path(__file__).resolve().parent
    for path in sorted(package_dir.glob("*.py")):
        if path.name in {"__init__.py", "base.py"}:
            continue
        import_module(f"{__name__}.{path.stem}")


def __getattr__(name: str) -> object:
    """Lazily resolve compatibility exports for flat `afkbot.models` imports."""

    module_name = _MODEL_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


__all__ = [
    *_MODEL_EXPORT_MODULES,
    "load_all_models",
]
