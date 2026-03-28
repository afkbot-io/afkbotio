"""Setup state helpers for local bootstrap and default-profile finalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.settings import Settings


SETUP_STATE_VERSION = 1
LEGACY_SETUP_STATE_RELPATH = "profiles/.system/install_state.json"


@dataclass(slots=True, frozen=True)
class SetupStateSnapshot:
    """Sanitized setup configuration saved in marker file."""

    env_file: str
    db_url: str
    llm_provider: str
    llm_model: str
    llm_thinking_level: str
    llm_proxy_type: str
    llm_proxy_configured: bool
    credentials_master_keys_configured: bool
    runtime_host: str
    runtime_port: int
    nginx_enabled: bool
    nginx_port: int
    public_runtime_url: str
    public_chat_api_url: str
    prompt_language: str
    policy_setup_mode: str
    policy_enabled: bool
    policy_preset: str
    policy_confirmation_mode: str
    policy_capabilities: tuple[str, ...]
    policy_allowed_tools: tuple[str, ...]
    policy_file_access_mode: str
    policy_allowed_directories: tuple[str, ...]
    policy_network_mode: str
    policy_network_allowlist: tuple[str, ...]


def setup_is_complete(settings: Settings) -> bool:
    """Return true when setup marker exists and has valid schema."""

    payload = read_setup_state_payload(settings)
    if payload is None:
        return False
    if payload.get("version") != SETUP_STATE_VERSION:
        return False
    if payload.get("completed") is not True:
        return False
    config = payload.get("config")
    return isinstance(config, dict)


def platform_is_bootstrapped(settings: Settings) -> bool:
    """Return true when persisted runtime config exists or source checkout is usable."""

    config = read_runtime_config(settings)
    if _runtime_config_is_bootstrapped(config):
        return True
    return manual_local_runtime_is_ready(settings)


def manual_local_runtime_is_ready(settings: Settings) -> bool:
    """Return whether the current root already looks like a runnable source checkout."""

    return (settings.root_dir / "pyproject.toml").exists() and (settings.root_dir / "afkbot").exists()


def _runtime_config_is_bootstrapped(config: dict[str, Any]) -> bool:
    required_fields = (
        "db_url",
        "runtime_host",
        "runtime_port",
    )
    return all(config.get(field) not in {None, ""} for field in required_fields)


def write_setup_state(settings: Settings, snapshot: SetupStateSnapshot) -> None:
    """Persist setup completion marker with sanitized config snapshot."""

    payload = build_setup_state_payload(snapshot, installed_at=datetime.now(tz=UTC))
    _atomic_json_write(settings.setup_state_path, payload)


def read_setup_state_payload(settings: Settings) -> dict[str, Any] | None:
    """Read raw setup-state payload when present and well-formed."""

    payload = _read_setup_state(settings.setup_state_path)
    if payload is not None:
        return payload
    return _read_setup_state(legacy_setup_state_path(settings))


def build_setup_state_payload(
    snapshot: SetupStateSnapshot,
    *,
    installed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build canonical setup-state payload from one sanitized snapshot."""

    if isinstance(installed_at, datetime):
        installed_at_value = installed_at.isoformat()
    elif isinstance(installed_at, str) and installed_at.strip():
        installed_at_value = installed_at.strip()
    else:
        installed_at_value = datetime.now(tz=UTC).isoformat()
    return {
        "version": SETUP_STATE_VERSION,
        "completed": True,
        "installed_at": installed_at_value,
        "config": {
            "env_file": snapshot.env_file,
            "db_url": snapshot.db_url,
            "llm_provider": snapshot.llm_provider,
            "llm_model": snapshot.llm_model,
            "llm_thinking_level": snapshot.llm_thinking_level,
            "llm_proxy_type": snapshot.llm_proxy_type,
            "llm_proxy_configured": snapshot.llm_proxy_configured,
            "credentials_master_keys_configured": snapshot.credentials_master_keys_configured,
            "runtime_host": snapshot.runtime_host,
            "runtime_port": snapshot.runtime_port,
            "nginx_enabled": snapshot.nginx_enabled,
            "nginx_port": snapshot.nginx_port,
            "public_runtime_url": snapshot.public_runtime_url,
            "public_chat_api_url": snapshot.public_chat_api_url,
            "prompt_language": snapshot.prompt_language,
            "policy_setup_mode": snapshot.policy_setup_mode,
            "policy_enabled": snapshot.policy_enabled,
            "policy_preset": snapshot.policy_preset,
            "policy_confirmation_mode": snapshot.policy_confirmation_mode,
            "policy_capabilities": list(snapshot.policy_capabilities),
            "policy_allowed_tools": list(snapshot.policy_allowed_tools),
            "policy_file_access_mode": snapshot.policy_file_access_mode,
            "policy_allowed_directories": list(snapshot.policy_allowed_directories),
            "policy_network_mode": snapshot.policy_network_mode,
            "policy_network_allowlist": list(snapshot.policy_network_allowlist),
        },
    }


def clear_setup_state(settings: Settings) -> None:
    """Remove setup marker if it exists."""

    for state_path in (settings.setup_state_path, legacy_setup_state_path(settings)):
        if state_path.exists():
            state_path.unlink()


def legacy_setup_state_path(settings: Settings) -> Path:
    """Return absolute path to the pre-rename install marker file."""

    return settings.root_dir / LEGACY_SETUP_STATE_RELPATH


def _read_setup_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
