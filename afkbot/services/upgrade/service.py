"""Idempotent post-update upgrade runner for persisted runtime state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.channel_endpoint import ChannelEndpoint
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.channels.endpoint_contracts import (
    deserialize_endpoint_config,
    serialize_endpoint_storage_payload,
)
from afkbot.services.setup.runtime_store import (
    RUNTIME_STORE_VERSION,
    read_runtime_config,
    write_runtime_config,
    write_runtime_secrets,
)
from afkbot.services.setup.state import (
    SetupStateSnapshot,
    build_setup_state_payload,
    legacy_setup_state_path,
    read_setup_state_payload,
    write_setup_state,
)
from afkbot.services.policy.file_access import default_allowed_directories
from afkbot.services.profile_runtime.contracts import ProfileRuntimeConfig
from afkbot.services.profile_runtime.runtime_config import (
    PROFILE_RUNTIME_CONFIG_VERSION,
    get_profile_runtime_config_service,
)
from afkbot.services.profile_runtime.runtime_secrets import (
    PROFILE_RUNTIME_SECRETS_VERSION,
    get_profile_runtime_secrets_service,
)
from afkbot.services.upgrade.contracts import UpgradeApplyReport, UpgradeStepReport
from afkbot.settings import Settings


class UpgradeService:
    """Apply one-shot upgrades to persisted non-code state after app updates."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime_configs = get_profile_runtime_config_service(settings)
        self._runtime_secrets = get_profile_runtime_secrets_service(settings)
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def apply(self) -> UpgradeApplyReport:
        """Run all idempotent upgrade steps and return one structured report."""

        return await self._run(apply_changes=True)

    async def inspect(self) -> UpgradeApplyReport:
        """Inspect pending persisted-state upgrades without mutating runtime state."""

        return await self._run(apply_changes=False)

    async def _run(self, *, apply_changes: bool) -> UpgradeApplyReport:
        """Run one upgrade pass in apply or dry-run mode."""

        steps = [
            self._upgrade_runtime_config_store(apply_changes=apply_changes),
            self._upgrade_runtime_secrets_store(apply_changes=apply_changes),
            self._upgrade_setup_state(apply_changes=apply_changes),
            self._upgrade_profile_runtime_configs(apply_changes=apply_changes),
            self._upgrade_profile_runtime_secrets(apply_changes=apply_changes),
        ]
        await self._ensure_schema()
        async with session_scope(self._session_factory) as session:
            steps.append(
                await self._upgrade_profile_policy_workspace_scope(
                    session,
                    apply_changes=apply_changes,
                )
            )
            steps.append(
                await self._upgrade_channel_endpoints(
                    session,
                    apply_changes=apply_changes,
                )
            )
        return UpgradeApplyReport(
            changed=any(item.changed for item in steps),
            steps=tuple(steps),
        )

    async def shutdown(self) -> None:
        """Dispose owned engine."""

        await self._engine.dispose()

    def _upgrade_runtime_config_store(self, *, apply_changes: bool) -> UpgradeStepReport:
        payload = self._read_json_object(self._settings.runtime_config_path)
        if not payload:
            return UpgradeStepReport(name="runtime_config_store", changed=False, details="no runtime config present")
        raw_config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
        if not isinstance(raw_config, dict):
            return UpgradeStepReport(name="runtime_config_store", changed=False, details="invalid runtime config ignored")
        canonical_config = {str(key): value for key, value in raw_config.items() if key != "version" and key != "updated_at"}
        if payload.get("version") == RUNTIME_STORE_VERSION and payload.get("config") == canonical_config:
            return UpgradeStepReport(name="runtime_config_store", changed=False, details="already canonical")
        if apply_changes:
            write_runtime_config(self._settings, config=canonical_config)
        return UpgradeStepReport(
            name="runtime_config_store",
            changed=True,
            details=(
                "rewrote runtime_config.json to canonical versioned payload"
                if apply_changes
                else "runtime_config.json needs canonical rewrite"
            ),
        )

    def _upgrade_runtime_secrets_store(self, *, apply_changes: bool) -> UpgradeStepReport:
        payload = self._read_json_object(self._settings.runtime_secrets_path)
        legacy_secrets = payload.get("secrets")
        if not isinstance(legacy_secrets, dict):
            return UpgradeStepReport(
                name="runtime_secrets_store",
                changed=False,
                details="already encrypted or absent",
            )
        normalized = {
            str(key): value.strip()
            for key, value in legacy_secrets.items()
            if isinstance(value, str) and value.strip()
        }
        if apply_changes:
            write_runtime_secrets(self._settings, secrets=normalized)
        return UpgradeStepReport(
            name="runtime_secrets_store",
            changed=True,
            details=(
                "migrated plaintext runtime secrets to encrypted store"
                if apply_changes
                else "plaintext runtime secrets need encrypted-store migration"
            ),
        )

    def _upgrade_setup_state(self, *, apply_changes: bool) -> UpgradeStepReport:
        payload = read_setup_state_payload(self._settings)
        legacy_path = legacy_setup_state_path(self._settings)
        legacy_present = legacy_path.exists()
        setup_present = self._settings.setup_state_path.exists()
        if payload is None:
            return UpgradeStepReport(name="setup_state", changed=False, details="no setup_state.json present")
        config = payload.get("config")
        if not isinstance(config, dict):
            return UpgradeStepReport(name="setup_state", changed=False, details="invalid setup_state ignored")

        runtime_config = read_runtime_config(self._settings)
        snapshot = SetupStateSnapshot(
            env_file=_coerce_text(config.get("env_file"), default=".unused"),
            db_url=_coerce_text(_first_present(config, runtime_config, "db_url"), default=self._settings.db_url),
            llm_provider=_coerce_text(
                _first_present(config, runtime_config, "llm_provider"),
                default="openai",
            ),
            llm_model=_coerce_text(
                _first_present(config, runtime_config, "llm_model"),
                default="gpt-4o-mini",
            ),
            llm_thinking_level=_coerce_text(
                _first_present(config, runtime_config, "llm_thinking_level"),
                default="medium",
            ),
            llm_proxy_type=_coerce_text(
                _first_present(config, runtime_config, "llm_proxy_type"),
                default="none",
            ),
            llm_proxy_configured=_coerce_bool(config.get("llm_proxy_configured")),
            credentials_master_keys_configured=_coerce_bool(config.get("credentials_master_keys_configured")),
            runtime_host=_coerce_text(_first_present(config, runtime_config, "runtime_host"), default="127.0.0.1"),
            runtime_port=_coerce_int(
                _first_present(config, runtime_config, "runtime_port"),
                default=self._settings.runtime_port,
            ),
            nginx_enabled=_coerce_bool(_first_present(config, runtime_config, "nginx_enabled")),
            nginx_port=_coerce_int(_first_present(config, runtime_config, "nginx_port"), default=80),
            public_runtime_url=_coerce_text(
                _first_present(config, runtime_config, "public_runtime_url"),
                default="",
            ),
            public_chat_api_url=_coerce_text(
                _first_present(config, runtime_config, "public_chat_api_url"),
                default="",
            ),
            prompt_language=_coerce_text(
                _first_present_any(config, runtime_config, "prompt_language", "install_language"),
                default="en",
            ),
            policy_setup_mode=_coerce_text(_first_present(config, runtime_config, "policy_setup_mode"), default="preset"),
            policy_enabled=_coerce_bool(_first_present(config, runtime_config, "policy_enabled"), default=True),
            policy_preset=_coerce_text(_first_present(config, runtime_config, "policy_preset"), default="medium"),
            policy_confirmation_mode=_coerce_text(config.get("policy_confirmation_mode"), default="confirm_file_destructive_ops"),
            policy_capabilities=_coerce_text_tuple(config.get("policy_capabilities")),
            policy_allowed_tools=_coerce_text_tuple(config.get("policy_allowed_tools")),
            policy_file_access_mode=_coerce_text(config.get("policy_file_access_mode"), default="read_write"),
            policy_allowed_directories=_coerce_text_tuple(
                _first_present(config, runtime_config, "policy_allowed_directories")
            ),
            policy_network_mode=_coerce_text(config.get("policy_network_mode"), default="custom"),
            policy_network_allowlist=_coerce_text_tuple(config.get("policy_network_allowlist")),
        )
        canonical_payload = build_setup_state_payload(
            snapshot,
            installed_at=payload.get("installed_at"),
        )
        if payload == canonical_payload and setup_present and not legacy_present:
            return UpgradeStepReport(name="setup_state", changed=False, details="already canonical")
        if apply_changes:
            write_setup_state(self._settings, snapshot)
            if legacy_present:
                legacy_path.unlink()
            raw = self._read_json_object(self._settings.setup_state_path)
            if isinstance(payload.get("installed_at"), str) and raw:
                raw["installed_at"] = payload["installed_at"]
                atomic_json_write(self._settings.setup_state_path, raw, mode=0o600)
        return UpgradeStepReport(
            name="setup_state",
            changed=True,
            details=(
                "rewrote setup_state.json to canonical payload and removed legacy marker when present"
                if apply_changes
                else "setup marker needs canonical rewrite or legacy-marker cleanup"
            ),
        )

    def _upgrade_profile_runtime_configs(self, *, apply_changes: bool) -> UpgradeStepReport:
        changed = 0
        for config_path in self._settings.profiles_dir.glob("*/.system/agent_config.json"):
            profile_id = config_path.parent.parent.name
            payload = self._read_json_object(config_path)
            raw_config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
            if not isinstance(raw_config, dict):
                continue
            config = ProfileRuntimeConfig.model_validate(raw_config)
            canonical_config = config.model_dump(mode="json", exclude_none=True)
            if payload.get("version") == PROFILE_RUNTIME_CONFIG_VERSION and payload.get("config") == canonical_config:
                continue
            if apply_changes:
                self._runtime_configs.write(profile_id, config)
            changed += 1
        return UpgradeStepReport(
            name="profile_runtime_configs",
            changed=changed > 0,
            details=(
                f"rewrote {changed} profile runtime config(s)"
                if apply_changes and changed
                else (
                    f"{changed} profile runtime config(s) need canonical rewrite"
                    if changed
                    else "already canonical"
                )
            ),
        )

    def _upgrade_profile_runtime_secrets(self, *, apply_changes: bool) -> UpgradeStepReport:
        changed = 0
        for secrets_path in self._settings.profiles_dir.glob("*/.system/agent_secrets.json"):
            profile_id = secrets_path.parent.parent.name
            payload = self._read_json_object(secrets_path)
            if (
                payload.get("version") == PROFILE_RUNTIME_SECRETS_VERSION
                and str(payload.get("encryption") or "").strip().lower() == "fernet-v1"
                and isinstance(payload.get("ciphertext"), str)
                and bool(str(payload.get("ciphertext") or "").strip())
            ):
                continue
            raw_secrets = payload.get("secrets") if isinstance(payload.get("secrets"), dict) else payload
            if not isinstance(raw_secrets, dict):
                continue
            normalized = {
                str(key): value
                for key, value in raw_secrets.items()
                if isinstance(value, str)
            }
            if apply_changes and not self._runtime_secrets.write(profile_id, normalized):
                continue
            changed += 1
        return UpgradeStepReport(
            name="profile_runtime_secrets",
            changed=changed > 0,
            details=(
                f"rewrote {changed} profile runtime secrets file(s)"
                if apply_changes and changed
                else (
                    f"{changed} profile runtime secrets file(s) need canonical rewrite"
                    if changed
                    else "already canonical"
                )
            ),
        )

    async def _upgrade_profile_policy_workspace_scope(
        self,
        session: AsyncSession,
        *,
        apply_changes: bool,
    ) -> UpgradeStepReport:
        result = await session.execute(select(ProfilePolicy).order_by(ProfilePolicy.profile_id.asc()))
        rows = list(result.scalars().all())
        if not rows:
            return UpgradeStepReport(name="profile_policy_workspace_scope", changed=False, details="no profile policies present")

        legacy_default_root = str(self._settings.root_dir.resolve(strict=False))
        changed = 0
        for row in rows:
            directories = _decode_json_list(getattr(row, "allowed_directories_json", "[]"))
            if directories != [legacy_default_root]:
                continue
            canonical = sorted(
                default_allowed_directories(
                    root_dir=self._settings.root_dir,
                    profile_root=self._runtime_configs.profile_root(row.profile_id),
                    profile_id=row.profile_id,
                )
            )
            if apply_changes:
                row.allowed_directories_json = json.dumps(canonical, ensure_ascii=True, sort_keys=True)
            changed += 1
        if changed and apply_changes:
            await session.flush()
        return UpgradeStepReport(
            name="profile_policy_workspace_scope",
            changed=changed > 0,
            details=(
                f"migrated {changed} legacy profile policy scope(s) to profile-local workspace"
                if apply_changes and changed
                else (
                    f"{changed} legacy profile policy scope(s) still use legacy project-root defaults"
                    if changed
                    else "already profile-local"
                )
            ),
        )

    async def _upgrade_channel_endpoints(
        self,
        session: AsyncSession,
        *,
        apply_changes: bool,
    ) -> UpgradeStepReport:
        result = await session.execute(select(ChannelEndpoint).order_by(ChannelEndpoint.endpoint_id.asc()))
        rows = list(result.scalars().all())
        changed = 0
        for row in rows:
            raw_config = self._decode_config_json(getattr(row, "config_json", "{}"))
            config = deserialize_endpoint_config(
                {
                    "endpoint_id": getattr(row, "endpoint_id"),
                    "transport": getattr(row, "transport"),
                    "adapter_kind": getattr(row, "adapter_kind"),
                    "profile_id": getattr(row, "profile_id"),
                    "credential_profile_key": getattr(row, "credential_profile_key"),
                    "account_id": getattr(row, "account_id"),
                    "enabled": getattr(row, "enabled"),
                    "group_trigger_mode": getattr(row, "group_trigger_mode", None),
                    "config": raw_config,
                }
            )
            canonical_group_trigger_mode, canonical_config = serialize_endpoint_storage_payload(config)
            canonical_json = json.dumps(canonical_config, ensure_ascii=True, sort_keys=True)
            if (
                str(getattr(row, "transport")) == config.transport
                and str(getattr(row, "adapter_kind")) == config.adapter_kind
                and str(getattr(row, "profile_id")) == config.profile_id
                and str(getattr(row, "credential_profile_key")) == config.credential_profile_key
                and str(getattr(row, "account_id")) == config.account_id
                and bool(getattr(row, "enabled")) == config.enabled
                and getattr(row, "group_trigger_mode", None) == canonical_group_trigger_mode
                and str(getattr(row, "config_json", "{}")) == canonical_json
            ):
                continue
            if apply_changes:
                row.transport = config.transport
                row.adapter_kind = config.adapter_kind
                row.profile_id = config.profile_id
                row.credential_profile_key = config.credential_profile_key
                row.account_id = config.account_id
                row.enabled = config.enabled
                row.group_trigger_mode = canonical_group_trigger_mode
                row.config_json = canonical_json
            changed += 1
        if changed and apply_changes:
            await session.flush()
        return UpgradeStepReport(
            name="channel_endpoints",
            changed=changed > 0,
            details=(
                f"canonicalized {changed} channel endpoint(s)"
                if apply_changes and changed
                else (
                    f"{changed} channel endpoint(s) need canonical rewrite"
                    if changed
                    else "already canonical"
                )
            ),
        )

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    @staticmethod
    def _decode_config_json(raw: str) -> dict[str, object]:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items()}



def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return int(normalized)
            except ValueError:
                return default
    return default


def _coerce_text(value: object, *, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            return normalized
    return default


def _coerce_text_tuple(value: object) -> tuple[str, ...]:
    raw_values: tuple[str, ...]
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = tuple(str(item) for item in value)
    else:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _decode_json_list(raw: str) -> list[str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item).strip() for item in payload if str(item).strip()]


def _first_present(primary: dict[str, object], fallback: dict[str, object], key: str) -> object:
    if key in primary:
        return primary.get(key)
    return fallback.get(key)


def _first_present_any(
    primary: dict[str, object],
    fallback: dict[str, object],
    *keys: str,
) -> object:
    for key in keys:
        if key in primary:
            return primary.get(key)
        if key in fallback:
            return fallback.get(key)
    return None
