"""Tests for persisted-state upgrade runner."""

from __future__ import annotations

import json
from pathlib import Path

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.channel_endpoint import ChannelEndpoint
from afkbot.models.profile import Profile
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.profile_runtime.runtime_secrets import get_profile_runtime_secrets_service
from afkbot.services.upgrade import UpgradeService
from afkbot.settings import Settings


async def test_upgrade_service_migrates_default_profile_policy_scope(tmp_path: Path) -> None:
    """Upgrade runner should move legacy default file scope from project root to profile root."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add(Profile(id="default", name="Default", is_default=True, status="active"))
        session.add(
            ProfilePolicy(
                profile_id="default",
                policy_enabled=True,
                policy_preset="medium",
                policy_capabilities_json="[]",
                allowed_tools_json="[]",
                denied_tools_json="[]",
                allowed_directories_json=json.dumps([str(tmp_path.resolve())], ensure_ascii=True),
                shell_allowed_commands_json="[]",
                shell_denied_commands_json="[]",
                network_allowlist_json="[]",
            )
        )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "profile_policy_workspace_scope")
    assert step.changed is True
    async with session_scope(session_factory) as session:
        row = await session.get(ProfilePolicy, "default")
        assert row is not None
        assert json.loads(row.allowed_directories_json) == [str((tmp_path / "profiles/default").resolve())]
    await engine.dispose()


async def test_upgrade_service_migrates_legacy_non_default_profile_policy_scope(tmp_path: Path) -> None:
    """Upgrade runner should also migrate legacy project-root scope for named profiles."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add(Profile(id="ops", name="Ops", is_default=False, status="active"))
        session.add(
            ProfilePolicy(
                profile_id="ops",
                policy_enabled=True,
                policy_preset="medium",
                policy_capabilities_json="[]",
                allowed_tools_json="[]",
                denied_tools_json="[]",
                allowed_directories_json=json.dumps([str(tmp_path.resolve())], ensure_ascii=True),
                shell_allowed_commands_json="[]",
                shell_denied_commands_json="[]",
                network_allowlist_json="[]",
            )
        )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "profile_policy_workspace_scope")
    assert step.changed is True
    async with session_scope(session_factory) as session:
        row = await session.get(ProfilePolicy, "ops")
        assert row is not None
        assert json.loads(row.allowed_directories_json) == [str((tmp_path / "profiles/ops").resolve())]
    await engine.dispose()


async def test_upgrade_service_canonicalizes_profile_runtime_config_payload(tmp_path: Path) -> None:
    """Upgrade runner should rewrite unversioned profile config payloads to canonical shape."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    config_path = tmp_path / "profiles/default/.system/agent_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
                "enabled_tool_plugins": ["bash_exec", "bash_exec", "file_read"],
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "profile_runtime_configs")
    assert step.changed is True
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["config"]["enabled_tool_plugins"] == ["bash_exec", "file_read"]


async def test_upgrade_service_canonicalizes_channel_endpoint_rows(tmp_path: Path) -> None:
    """Upgrade runner should rewrite endpoint rows to normalized lowercase/canonical config JSON."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    async with session_scope(session_factory) as session:
        session.add(Profile(id="default", name="Default", is_default=True, status="active"))
        await session.flush()
        session.add(
            ChannelEndpoint(
                endpoint_id="personal-user",
                transport="telegram_user",
                adapter_kind="telethon_userbot",
                profile_id="default",
                credential_profile_key="TG-USER",
                account_id="Personal-User",
                enabled=True,
                group_trigger_mode=None,
                config_json=json.dumps(
                    {
                        "reply_mode": "same_chat",
                        "reply_allowed_chat_patterns": ["Andrey", "Andrey"],
                    },
                    ensure_ascii=True,
                ),
            )
        )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "channel_endpoints")
    assert step.changed is True
    async with session_scope(session_factory) as session:
        row = await session.get(ChannelEndpoint, "personal-user")
        assert row is not None
        assert row.credential_profile_key == "tg-user"
        assert row.account_id == "personal-user"
        assert json.loads(row.config_json)["reply_allowed_chat_patterns"] == ["andrey"]
    await engine.dispose()


async def test_upgrade_service_migrates_legacy_install_state_marker(tmp_path: Path) -> None:
    """Upgrade runner should rewrite the old install-state path to setup-state and remove the legacy file."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    legacy_path = tmp_path / "profiles/.system/install_state.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "completed": True,
                "installed_at": "2026-03-05T00:00:00+00:00",
                "config": {
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o-mini",
                },
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "setup_state")
    assert step.changed is True
    assert not legacy_path.exists()
    assert settings.setup_state_path.exists()


async def test_upgrade_service_inspect_reports_pending_without_mutating(tmp_path: Path) -> None:
    """Dry-run inspection should detect pending setup migration without rewriting files."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    legacy_path = tmp_path / "profiles/.system/install_state.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "completed": True,
                "installed_at": "2026-03-05T00:00:00+00:00",
                "config": {
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o-mini",
                },
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    service = UpgradeService(settings)
    try:
        report = await service.inspect()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "setup_state")
    assert step.changed is True
    assert legacy_path.exists()
    assert not settings.setup_state_path.exists()


async def test_upgrade_service_migrates_legacy_profile_runtime_secrets(tmp_path: Path) -> None:
    """Upgrade runner should rewrite plaintext profile-local secrets to encrypted canonical payload."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    secrets_path = tmp_path / "profiles/default/.system/agent_secrets.json"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        json.dumps(
            {
                "openai_api_key": "sk-test",
                "brave_api_key": "brave-test",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    service = UpgradeService(settings)
    try:
        report = await service.apply()
    finally:
        await service.shutdown()

    step = next(item for item in report.steps if item.name == "profile_runtime_secrets")
    assert step.changed is True
    payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["encryption"] == "fernet-v1"
    assert isinstance(payload["ciphertext"], str)
    runtime_secrets = get_profile_runtime_secrets_service(settings).load("default")
    assert runtime_secrets == {
        "openai_api_key": "sk-test",
        "brave_api_key": "brave-test",
    }
