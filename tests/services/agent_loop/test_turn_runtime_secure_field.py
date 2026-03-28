"""Tests for secure-field submit anti-replay behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.pending_secure_request_repo import PendingSecureRequestRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.turn_runtime import submit_secure_field
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


@pytest.mark.asyncio
async def test_submit_secure_field_rejects_replay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Secure submit should consume pending request and reject replay attempts."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'secure_submit.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_scope(session_factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(
            session_id="cli-session",
            profile_id="default",
        )
        run = await RunRepository(session).create_run(
            session_id="cli-session",
            profile_id="default",
            status="completed",
        )
        await PendingSecureRequestRepository(session).create(
            profile_id="default",
            session_id="cli-session",
            run_id=run.id,
            question_id="secure:qid-1",
            secure_field="telegram_token",
            integration_name="telegram",
            credential_name="telegram_token",
            credential_profile_key="default",
            tool_name="app.run",
            nonce="nonce-1",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    captured: dict[str, object] = {}

    class _FakeCredentialsService:
        async def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace()

    monkeypatch.setattr("afkbot.services.agent_loop.turn_runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.turn_runtime.get_credentials_service",
        lambda _settings: _FakeCredentialsService(),
    )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="secure submit",
        question_id="secure:qid-1",
        secure_field="telegram_token",
        spec_patch={"secure_nonce": "nonce-1"},
    )

    ok1, code1 = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=envelope,
        secret_value="  secret-1  ",
    )
    ok2, code2 = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=envelope,
        secret_value="secret-2",
    )

    assert (ok1, code1) == (True, "ok")
    assert (ok2, code2) == (False, "secure_request_invalid_or_expired")
    assert captured["secret_value"] == "  secret-1  "

    await engine.dispose()


@pytest.mark.asyncio
async def test_submit_secure_field_releases_claim_after_credentials_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failed credential write should release claim so user can retry same secure request."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'secure_submit_retry.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_scope(session_factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(
            session_id="cli-session",
            profile_id="default",
        )
        run = await RunRepository(session).create_run(
            session_id="cli-session",
            profile_id="default",
            status="completed",
        )
        await PendingSecureRequestRepository(session).create(
            profile_id="default",
            session_id="cli-session",
            run_id=run.id,
            question_id="secure:qid-retry",
            secure_field="telegram_token",
            integration_name="telegram",
            credential_name="telegram_token",
            credential_profile_key="default",
            tool_name="app.run",
            nonce="nonce-retry",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    class _FlakyCredentialsService:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: object) -> object:
            _ = kwargs
            self.calls += 1
            if self.calls == 1:
                raise CredentialsServiceError(
                    error_code="credentials_conflict",
                    reason="simulated conflict",
                )
            return SimpleNamespace()

    service = _FlakyCredentialsService()
    monkeypatch.setattr("afkbot.services.agent_loop.turn_runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.turn_runtime.get_credentials_service",
        lambda _settings: service,
    )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="secure submit",
        question_id="secure:qid-retry",
        secure_field="telegram_token",
        spec_patch={"secure_nonce": "nonce-retry"},
    )

    first = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=envelope,
        secret_value="secret-1",
    )
    second = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=envelope,
        secret_value="secret-2",
    )

    assert first == (False, "credentials_conflict")
    assert second == (True, "ok")
    assert service.calls == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_submit_secure_field_uses_pending_request_generated_by_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Secure submit should validate question_id+nonce generated by AgentLoop."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    security_skill_dir = tmp_path / "afkbot/skills/security-secrets"
    telegram_skill_dir = tmp_path / "afkbot/skills/telegram"
    bootstrap_dir.mkdir(parents=True)
    security_skill_dir.mkdir(parents=True)
    telegram_skill_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    (security_skill_dir / "SKILL.md").write_text("# security-secrets", encoding="utf-8")
    (telegram_skill_dir / "SKILL.md").write_text("# telegram", encoding="utf-8")

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'secure_submit_from_loop.db'}",
        root_dir=tmp_path,
        llm_provider="custom",
        llm_model="test-model",
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_scope(session_factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            secure_request_ttl_sec=900,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="cli-session",
            message="send telegram",
            planned_tool_calls=[
                ToolCall(
                    name="app.run",
                    params={
                        "app_name": "telegram",
                        "action": "send_message",
                        "params": {"text": "hello"},
                    },
                )
            ],
        )
        assert result.envelope.action == "request_secure_field"

    class _FakeCredentialsService:
        async def create(self, **kwargs: object) -> object:
            _ = kwargs
            return SimpleNamespace()

    monkeypatch.setattr("afkbot.services.agent_loop.turn_runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.turn_runtime.get_credentials_service",
        lambda _settings: _FakeCredentialsService(),
    )

    secure_envelope = result.envelope
    ok, code = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=secure_envelope,
        secret_value="secret-1",
    )
    assert (ok, code) == (True, "ok")

    replay_ok, replay_code = await submit_secure_field(
        profile_id="default",
        session_id="cli-session",
        envelope=secure_envelope,
        secret_value="secret-2",
    )
    assert (replay_ok, replay_code) == (False, "secure_request_invalid_or_expired")

    await engine.dispose()


@pytest.mark.asyncio
async def test_submit_secure_field_concurrent_claim_single_use(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent submit attempts should allow exactly one successful claim."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'secure_submit_concurrent.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_scope(session_factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(
            session_id="cli-session",
            profile_id="default",
        )
        run = await RunRepository(session).create_run(
            session_id="cli-session",
            profile_id="default",
            status="completed",
        )
        await PendingSecureRequestRepository(session).create(
            profile_id="default",
            session_id="cli-session",
            run_id=run.id,
            question_id="secure:qid-concurrent",
            secure_field="telegram_token",
            integration_name="telegram",
            credential_name="telegram_token",
            credential_profile_key="default",
            tool_name="app.run",
            nonce="nonce-concurrent",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    class _SlowCredentialsService:
        async def create(self, **kwargs: object) -> object:
            _ = kwargs
            await asyncio.sleep(0.05)
            return SimpleNamespace()

    monkeypatch.setattr("afkbot.services.agent_loop.turn_runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "afkbot.services.agent_loop.turn_runtime.get_credentials_service",
        lambda _settings: _SlowCredentialsService(),
    )

    envelope = ActionEnvelope(
        action="request_secure_field",
        message="secure submit",
        question_id="secure:qid-concurrent",
        secure_field="telegram_token",
        spec_patch={"secure_nonce": "nonce-concurrent"},
    )

    first, second = await asyncio.gather(
        submit_secure_field(
            profile_id="default",
            session_id="cli-session",
            envelope=envelope,
            secret_value="secret-1",
        ),
        submit_secure_field(
            profile_id="default",
            session_id="cli-session",
            envelope=envelope,
            secret_value="secret-2",
        ),
    )

    outcomes = {first, second}
    assert (True, "ok") in outcomes
    assert (False, "secure_request_invalid_or_expired") in outcomes

    await engine.dispose()
