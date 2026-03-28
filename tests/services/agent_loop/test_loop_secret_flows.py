"""AgentLoop tests for secret handling and secure credential workflows."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.models.runlog_event import RunlogEvent
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.credentials import get_credentials_service
from afkbot.services.memory import reset_memory_services
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings
from tests.services.agent_loop._loop_harness import create_test_db


async def test_app_tool_reads_skill_and_requests_secure_field(tmp_path: Path) -> None:
    """App tool call should route by app_name and continue secure-flow path."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_skill_gate_allow.db")
    settings.credentials_master_keys = Fernet.generate_key().decode("utf-8")

    core_skill = tmp_path / "afkbot/skills/telegram"
    core_skill.mkdir(parents=True, exist_ok=True)
    (core_skill / "SKILL.md").write_text("# telegram", encoding="utf-8")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-skill-gate-allow",
            message="send telegram",
            planned_tool_calls=[
                ToolCall(
                    name="app.run",
                    params={
                        "app_name": "telegram",
                        "action": "send_message",
                        "params": {"text": "my password is qwerty"},
                    },
                )
            ],
        )

        assert result.envelope.action == "request_secure_field"
        assert result.envelope.question_id is not None
        assert result.envelope.spec_patch is not None
        assert "secure_nonce" in result.envelope.spec_patch
        assert result.envelope.spec_patch.get("tool_name") == "app.run"
        assert isinstance(result.envelope.spec_patch.get("tool_params"), dict)

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert any(event.event_type == "turn.request_secure_field" for event in events)
        secure_event = [
            event for event in events if event.event_type == "turn.request_secure_field"
        ][0]
        secure_payload = json.loads(secure_event.payload_json)
        assert "qwerty" not in secure_event.payload_json
        spec_patch = secure_payload.get("spec_patch")
        assert isinstance(spec_patch, dict)
        tool_params = spec_patch.get("tool_params")
        assert isinstance(tool_params, dict)
        assert "qwerty" not in json.dumps(tool_params, ensure_ascii=True)
        assert "[REDACTED]" in json.dumps(tool_params, ensure_ascii=True)

    await engine.dispose()


async def test_run_turn_blocks_secret_input_in_chat_flow(tmp_path: Path) -> None:
    """Secret-like user text in chat flow must short-circuit with block envelope."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_secret_block.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(session, ContextBuilder(settings, SkillLoader(settings)))
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-secret-input",
            message="my token=abc123",
        )

        assert result.envelope.action == "block"
        assert result.envelope.blocked_reason == "security_secret_input_blocked"
        assert "abc123" not in result.envelope.message

        turns = (await session.execute(select(ChatTurn))).scalars().all()
        assert len(turns) == 1
        assert "abc123" not in turns[0].user_message
        assert "[REDACTED]" in turns[0].user_message

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert [event.event_type for event in events] == ["turn.block", "turn.finalize"]
        payload_dump = "\n".join(event.payload_json for event in events)
        assert "abc123" not in payload_dump

    await engine.dispose()


async def test_run_turn_blocks_secret_input_with_redacted_prefix(tmp_path: Path) -> None:
    """Guard must block secret input even if message already contains [REDACTED]."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_secret_block_redacted.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(session, ContextBuilder(settings, SkillLoader(settings)))
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-secret-redacted-prefix",
            message="my password is [REDACTED] qwerty",
        )

        assert result.envelope.action == "block"
        assert result.envelope.blocked_reason == "security_secret_input_blocked"

        turns = (await session.execute(select(ChatTurn))).scalars().all()
        assert len(turns) == 1
        assert "qwerty" not in turns[0].user_message

        payload_dump = "\n".join(
            event.payload_json
            for event in (
                (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
                .scalars()
                .all()
            )
        )
        assert "qwerty" not in payload_dump

    await engine.dispose()


async def test_credentials_tool_requires_secure_field_capture(tmp_path: Path) -> None:
    """credentials.create must be denied with secret capture envelope and redacted in runlog."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_credentials_guard.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-cred-guard",
            message="save creds",
            planned_tool_calls=[
                ToolCall(
                    name="credentials.create",
                    params={
                        "app_name": "telegram",
                        "profile_name": "default",
                        "credential_slug": "telegram_token",
                        "value": "short-secret",
                    },
                )
            ],
        )
        assert result.envelope.action == "request_secure_field"
        assert result.envelope.secure_field == "telegram_token"
        assert result.envelope.question_id is not None
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch.get("tool_name") in {"", None}
        assert result.envelope.spec_patch.get("error_code") == "security_secure_input_required"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        call_payload = json.loads(
            [event for event in events if event.event_type == "tool.call"][0].payload_json
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )

        assert call_payload["name"] == "credentials.create"
        assert call_payload["params"]["value"] == "[REDACTED]"
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "security_secure_input_required"
        assert any(event.event_type == "turn.request_secure_field" for event in events)

        payload_dump = "\n".join(
            event.payload_json for event in events if event.event_type.startswith("tool.")
        )
        assert "short-secret" not in payload_dump

    await engine.dispose()


async def test_app_run_with_multiple_credential_profiles_requests_profile_selection(
    tmp_path: Path,
) -> None:
    """Multiple available credential profiles should ask for profile choice, not secure input."""

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
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'loop_profile_selection.db'}",
        root_dir=tmp_path,
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    service = get_credentials_service(settings)
    await service.create(
        profile_id="default",
        tool_name="app.run",
        integration_name="telegram",
        credential_profile_key="work",
        credential_name="telegram_token",
        secret_value="token-work",
        replace_existing=True,
    )
    await service.create(
        profile_id="default",
        tool_name="app.run",
        integration_name="telegram",
        credential_profile_key="personal",
        credential_name="telegram_token",
        secret_value="token-personal",
        replace_existing=True,
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-profile-selection",
            message="send telegram",
            planned_tool_calls=[
                ToolCall(
                    name="app.run",
                    params={
                        "app_name": "telegram",
                        "action": "get_me",
                        "params": {},
                    },
                )
            ],
        )

        assert result.envelope.action == "ask_question"
        assert result.envelope.secure_field is None
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch["question_kind"] == "credential_profile_required"
        assert result.envelope.spec_patch["tool_name"] == "app.run"
        assert result.envelope.spec_patch["available_profile_keys"] == ["personal", "work"]

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert any(event.event_type == "turn.ask_question" for event in events)
        assert not any(event.event_type == "turn.request_secure_field" for event in events)
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["error_code"] == "credential_profile_required"

    await engine.dispose()


async def test_memory_tool_logs_redact_content_and_query(tmp_path: Path) -> None:
    """Runlog payloads must not persist plaintext memory content/query fields."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_memory_redaction.db")
    reset_memory_services()

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="default",
            session_id="s-memory-redaction",
            message="remember food",
            planned_tool_calls=[
                ToolCall(
                    name="memory.upsert",
                    params={"memory_key": "food", "content": "user likes pasta", "source": "chat"},
                ),
                ToolCall(
                    name="memory.search",
                    params={"query": "what food does user like", "limit": 5},
                ),
            ],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        tool_calls = [
            json.loads(event.payload_json) for event in events if event.event_type == "tool.call"
        ]
        tool_results = [
            json.loads(event.payload_json) for event in events if event.event_type == "tool.result"
        ]

        upsert_call = tool_calls[0]
        assert upsert_call["name"] == "memory.upsert"
        assert upsert_call["params"]["content"] == "[REDACTED]"

        search_call = tool_calls[1]
        assert search_call["name"] == "memory.search"
        assert search_call["params"]["query"] == "[REDACTED]"

        upsert_result = tool_results[0]
        assert upsert_result["name"] == "memory.upsert"
        if upsert_result["result"]["ok"]:
            assert upsert_result["result"]["payload"]["item"]["content"] == "[REDACTED]"
        else:
            assert upsert_result["result"]["reason"] == "[REDACTED]"

        search_result = tool_results[1]
        assert search_result["name"] == "memory.search"
        if search_result["result"]["payload"]["items"]:
            assert search_result["result"]["payload"]["items"][0]["content"] == "[REDACTED]"

        payload_dump = "\n".join(
            event.payload_json for event in events if event.event_type.startswith("tool.")
        )
        assert "user likes pasta" not in payload_dump
        assert "what food does user like" not in payload_dump

    await engine.dispose()
