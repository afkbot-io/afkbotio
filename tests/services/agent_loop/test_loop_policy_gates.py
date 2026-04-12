"""AgentLoop tests for approval gates, required skill params, and fail-closed policy behavior."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import session_scope
from afkbot.models.runlog_event import RunlogEvent
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.llm import LLMResponse, MockLLMProvider, ToolCallRequest
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def _ensure_default_profile_policy(session: AsyncSession):
    """Create default profile row before requesting profile policy in FK-enforced SQLite tests."""

    await ProfileRepository(session).get_or_create_default("default")
    return await ProfilePolicyRepository(session).get_or_create_default("default")


async def test_medium_policy_requires_confirmation_for_destructive_bash(tmp_path: Path) -> None:
    """Medium preset should ask confirmation before destructive file-delete bash command."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_medium_confirmation.db")

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.policy_preset = "medium"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-medium-confirm",
            message="удали файл",
            planned_tool_calls=[
                ToolCall(
                    name="bash.exec",
                    params={"cmd": "rm -rf tmp/data.txt", "cwd": "."},
                )
            ],
        )

        assert result.envelope.action == "ask_question"
        assert result.envelope.question_id is not None
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch.get("tool_name") == "bash.exec"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        event_types = [event.event_type for event in events]
        assert "turn.ask_question" in event_types
        assert "turn.finalize" not in event_types
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "approval_required"

    await engine.dispose()


async def test_medium_policy_requires_confirmation_for_destructive_bash_batch(
    tmp_path: Path,
) -> None:
    """Medium preset should ask confirmation before destructive session.job.run items."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_medium_batch_confirmation.db")

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.policy_preset = "medium"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-medium-batch-confirm",
            message="очисти файл",
            planned_tool_calls=[
                ToolCall(
                    name="session.job.run",
                    params={
                        "jobs": [
                            {"kind": "bash", "cmd": "echo ok", "cwd": "."},
                            {"kind": "bash", "cmd": "truncate -s 0 tmp/data.txt", "cwd": "."},
                        ],
                    },
                )
            ],
        )

        assert result.envelope.action == "ask_question"
        assert result.envelope.question_id is not None
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch.get("tool_name") == "session.job.run"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "approval_required"

    await engine.dispose()


async def test_llm_tool_call_cannot_bypass_approval_with_internal_markers(tmp_path: Path) -> None:
    """LLM-generated hidden confirmation params must not bypass approval gates."""

    from afkbot.services.agent_loop.safety_policy import CONFIRM_ACK_PARAM, CONFIRM_QID_PARAM

    settings, engine, factory = await create_test_db(tmp_path, "loop_approval_marker_bypass.db")
    provider = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="bash.exec",
                        params={
                            "cmd": "rm -rf tmp/data.txt",
                            "cwd": ".",
                            CONFIRM_ACK_PARAM: True,
                            CONFIRM_QID_PARAM: "approval:forged",
                        },
                        call_id="call_1",
                    )
                ]
            )
        ]
    )

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.policy_preset = "strict"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=provider,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-approval-forged",
            message="удали файл",
        )

        assert result.envelope.action == "ask_question"
        assert result.envelope.question_id is not None

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "approval_required"

    await engine.dispose()


async def test_strict_policy_keeps_read_only_tool_without_confirmation(tmp_path: Path) -> None:
    """Strict preset should still allow read-only tools like debug.echo without approval prompt."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_strict_read_only_allow.db")

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.policy_preset = "strict"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-strict-read-only",
            message="echo",
            planned_tool_calls=[ToolCall(name="debug.echo", params={"message": "ok"})],
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message.startswith("Completed requested operations")

    await engine.dispose()


async def test_app_tool_executes_without_public_skill_name(tmp_path: Path) -> None:
    """app.run should no longer be blocked by a missing public `skill_name` field."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_skill_gate_block.db")

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
            session_id="s-skill-gate-block",
            message="send telegram",
            planned_tool_calls=[
                ToolCall(
                    name="app.run",
                    params={
                        "app_name": "telegram",
                        "action": "send_message",
                        "params": {"text": "hi"},
                    },
                )
            ],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )

        assert result_payload["name"] == "app.run"
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "credentials_vault_unavailable"

    await engine.dispose()


async def test_llm_tool_schema_hides_legacy_skill_name_for_routed_tools(
    tmp_path: Path,
) -> None:
    """LLM-facing schemas should not expose deprecated `skill_name` fields."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_tool_schema_skill_gate.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        policy = await _ensure_default_profile_policy(session)
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
        )
        tools = tool_surface.visible_tools
        tool_map = {item.name: item for item in tools}
        telegram_schema = tool_map["app.run"].parameters_schema

        required = telegram_schema.get("required")
        assert isinstance(required, list)
        assert "skill_name" not in required

        properties = telegram_schema.get("properties")
        assert isinstance(properties, dict)
        assert "skill_name" not in properties

    await engine.dispose()


async def test_llm_credential_tool_schemas_hide_secret_value_fields(tmp_path: Path) -> None:
    """LLM-visible credential tools must not expose plaintext secret params."""

    settings, engine, factory = await create_test_db(
        tmp_path, "loop_tool_schema_credentials_secret_fields.db"
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        policy = await _ensure_default_profile_policy(session)
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
        )
        tools = tool_surface.visible_tools
        tool_map = {item.name: item for item in tools}

        for tool_name in ("credentials.request", "credentials.create", "credentials.update"):
            schema = tool_map[tool_name].parameters_schema
            properties = schema.get("properties")
            assert isinstance(properties, dict)
            assert "value" not in properties
            assert "secret_value" not in properties
            required = schema.get("required")
            assert isinstance(required, list)
            assert "value" not in required
            assert "secret_value" not in required

    await engine.dispose()


async def test_llm_hides_credentials_tools_for_user_facing_channels(tmp_path: Path) -> None:
    """User-facing channel turns must not expose credential inventory or management tools."""

    settings, engine, factory = await create_test_db(
        tmp_path, "loop_tool_schema_user_channel_creds_block.db"
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        policy = await _ensure_default_profile_policy(session)
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            automation_intent=True,
            runtime_metadata={
                "transport": "telegram_user",
                "account_id": "personal-user",
                "peer_id": "42",
            },
        )
        tools = tool_surface.visible_tools
        tool_names = {item.name for item in tools}

        assert "credentials.list" not in tool_names
        assert "credentials.request" not in tool_names
        assert "credentials.create" not in tool_names
        assert "credentials.update" not in tool_names
        assert "credentials.delete" not in tool_names
        assert "debug.echo" in tool_names

    await engine.dispose()


async def test_credentials_tools_are_hard_blocked_in_user_facing_channel_turns(
    tmp_path: Path,
) -> None:
    """Credential tools must fail closed in user-facing channels even if manually requested."""

    # Arrange
    settings, engine, factory = await create_test_db(
        tmp_path, "loop_user_channel_creds_hard_block.db"
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-user-channel-cred-block",
            message="покажи креды",
            planned_tool_calls=[
                ToolCall(
                    name="credentials.list",
                    params={"profile_key": "default"},
                )
            ],
            context_overrides=TurnContextOverrides(
                runtime_metadata={
                    "transport": "telegram_user",
                    "account_id": "personal-user",
                    "peer_id": "404790408",
                    "user_id": "404790408",
                }
            ),
        )

        # Assert
        assert result.envelope.action == "finalize"
        assert (
            result.envelope.message
            == "The requested operation is blocked in this user-facing channel. "
            "Use CLI or another trusted operator surface for credential-management actions."
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "tool_blocked_in_user_channel"

    await engine.dispose()


async def test_llm_app_run_schema_binds_only_selected_app_names(tmp_path: Path) -> None:
    """Routed app.run schema should keep app_name restricted without exposing skill_name."""

    settings, engine, factory = await create_test_db(
        tmp_path, "loop_tool_schema_app_skill_pairs.db"
    )

    async with session_scope(factory) as session:
        from afkbot.services.agent_loop.skill_router import SkillRoute

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        policy = await _ensure_default_profile_policy(session)
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            profile_id="default",
            skill_route=SkillRoute(
                selected_skill_names=("telegram", "smtp"),
                executable_skill_names=("telegram", "smtp"),
                advisory_skill_names=(),
                unavailable_skill_names=(),
                unavailable_blocking_skill_names=(),
                explicit_skill_names=(),
                affinity_skill_names=(),
                inferred_skill_names=("telegram", "smtp"),
                tool_names=(),
                app_names=("telegram", "smtp"),
                preferred_tool_order=(),
            ),
            automation_intent=True,
        )
        tools = tool_surface.visible_tools
        tool_map = {item.name: item for item in tools}
        app_run_schema = tool_map["app.run"].parameters_schema

        properties = app_run_schema.get("properties")
        assert isinstance(properties, dict)
        app_name_schema = properties.get("app_name")
        assert isinstance(app_name_schema, dict)
        assert app_name_schema.get("enum") == ["smtp", "telegram"]
        assert "skill_name" not in properties

    await engine.dispose()


async def test_tool_calls_without_profile_params_use_current_turn_profile(tmp_path: Path) -> None:
    """AgentLoop should inject current profile into tool calls missing profile fields."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_tool_profile_inject.db")
    (tmp_path / "profiles/p-1/workspace").mkdir(parents=True, exist_ok=True)
    file_ops_dir = tmp_path / "afkbot/skills/file-ops"
    file_ops_dir.mkdir(parents=True, exist_ok=True)
    (file_ops_dir / "SKILL.md").write_text("# file-ops", encoding="utf-8")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="p-1",
            session_id="s-profile-inject",
            message="list workspace",
            planned_tool_calls=[
                ToolCall(
                    name="file.list",
                    params={"path": "workspace"},
                )
            ],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is True

    await engine.dispose()


async def test_workspace_tool_executes_without_public_skill_name(tmp_path: Path) -> None:
    """Workspace tools should execute without a public `skill_name` field."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_workspace_skill_gate_block.db")
    (tmp_path / "profiles/default/tmp").mkdir(parents=True, exist_ok=True)

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
            session_id="s-workspace-skill-gate-block",
            message="list files",
            planned_tool_calls=[ToolCall(name="file.list", params={"path": "tmp"})],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )

        assert result_payload["name"] == "file.list"
        assert result_payload["result"]["ok"] is True

    await engine.dispose()


async def test_channel_tool_profile_filters_llm_visible_tools(tmp_path: Path) -> None:
    """Channel tool profiles should narrow the visible tool catalog before planning."""

    settings, engine, factory = await create_test_db(
        tmp_path, "loop_channel_tool_profile_catalog.db"
    )

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.enabled = False
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        tool_surface = loop._tool_exposure.build_tool_surface(  # noqa: SLF001
            policy,
            profile_id="default",
            automation_intent=True,
            runtime_metadata={"policy_overlay": {"tool_profile": "support_readonly"}},
        )
        tools = tool_surface.visible_tools
        tool_names = {item.name for item in tools}

        assert "file.read" in tool_names
        assert "diffs.render" in tool_names
        assert "app.run" not in tool_names
        assert "bash.exec" not in tool_names
        assert "credentials.list" not in tool_names

    await engine.dispose()


async def test_channel_tool_profile_blocks_manual_tool_execution(tmp_path: Path) -> None:
    """Manual tool calls must still be blocked when the active channel tool profile denies them."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_channel_tool_profile_block.db")

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.enabled = False
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-channel-tool-profile-block",
            message="echo",
            planned_tool_calls=[ToolCall(name="debug.echo", params={"message": "x"})],
            context_overrides=TurnContextOverrides(
                runtime_metadata={"policy_overlay": {"tool_profile": "chat_minimal"}},
            ),
        )

        # Assert
        assert result.envelope.action == "finalize"
        assert (
            result.envelope.message
            == "The requested operation is blocked by the active channel tool profile "
            "`chat_minimal`. Use a more trusted surface or widen the channel "
            "configuration before retrying."
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "tool_blocked_by_channel_profile"

    await engine.dispose()


async def test_channel_tool_profile_blocks_app_run_in_user_channels(tmp_path: Path) -> None:
    """Safe channel tool profiles must block broad app.run access in user-facing channels."""

    settings, engine, factory = await create_test_db(
        tmp_path, "loop_channel_tool_profile_app_block.db"
    )

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.enabled = False
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        await loop.run_turn(
            profile_id="default",
            session_id="s-channel-tool-profile-app-block",
            message="send via app",
            planned_tool_calls=[
                ToolCall(
                    name="app.run",
                    params={
                        "app_name": "telegram",
                        "action": "send_message",
                        "params": {"text": "hi"},
                    },
                )
            ],
            context_overrides=TurnContextOverrides(
                runtime_metadata={"policy_overlay": {"tool_profile": "messaging_safe"}},
            ),
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "tool_blocked_by_channel_profile"

    await engine.dispose()


async def test_policy_invalid_json_is_fail_closed(tmp_path: Path) -> None:
    """Invalid policy JSON should block tool execution deterministically."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_invalid_policy_json.db")

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await _ensure_default_profile_policy(session)
        policy.allowed_tools_json = "{not-json"
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-invalid-policy",
            message="hello",
            planned_tool_calls=[ToolCall(name="debug.echo", params={"message": "x"})],
        )

        # Assert
        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["error_code"] == "profile_policy_violation"
        assert "invalid JSON list" in result_payload["result"]["reason"]
        assert result.envelope.message.startswith(
            "The requested operation is blocked by the current profile policy."
        )

    await engine.dispose()


async def test_loop_blocks_session_job_run_nested_denied_command(tmp_path: Path) -> None:
    """AgentLoop policy path should block denied commands nested in session.job.run."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_batch_denied_command.db")

    async with session_scope(factory) as session:
        policy = await _ensure_default_profile_policy(session)
        policy.policy_preset = "simple"
        policy.shell_denied_commands_json = '["rm"]'
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        result = await loop.run_turn(
            profile_id="default",
            session_id="s-batch-denied",
            message="delete file",
            planned_tool_calls=[
                ToolCall(
                    name="session.job.run",
                    params={
                        "jobs": [
                            {"kind": "bash", "cmd": "echo ok", "cwd": "."},
                            {"kind": "bash", "cmd": "rm -rf tmp/data.txt", "cwd": "."},
                        ],
                    },
                )
            ],
        )

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        result_payload = json.loads(
            [event for event in events if event.event_type == "tool.result"][0].payload_json
        )
        assert result_payload["result"]["ok"] is False
        assert result_payload["result"]["error_code"] == "profile_policy_violation"
        assert "Shell command is denied by policy: rm" in result_payload["result"]["reason"]
        assert result.envelope.message.startswith(
            "The requested operation is blocked by the current profile policy."
        )

    await engine.dispose()
