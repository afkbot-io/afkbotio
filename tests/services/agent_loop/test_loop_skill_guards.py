"""AgentLoop tests for explicit skill/subagent guard behavior."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from afkbot.db.session import session_scope
from afkbot.models.runlog_event import RunlogEvent
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.llm import LLMResponse, MockLLMProvider, ToolCallRequest
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def test_explicit_skill_guard_does_not_retry_on_llm_error_response(
    tmp_path: Path,
) -> None:
    """Explicit skill requests should not override provider error final responses."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_error_no_retry.db")
    skill_dir = tmp_path / "afkbot/skills/subagent-manager"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# subagent-manager", encoding="utf-8")
    scripted = MockLLMProvider(
        [
            LLMResponse.final(
                "LLM provider is temporarily unavailable. Please try again shortly.",
                error_code="llm_provider_network_error",
            )
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-guard-error",
            message="Use subagent-manager and create a worker",
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "LLM provider is temporarily unavailable. Please try again shortly."
        assert len(scripted.requests) == 1

    await engine.dispose()


async def test_explicit_skill_reference_can_finalize_without_tool_execution(tmp_path: Path) -> None:
    """Explicit skill references should remain guidance when the model finalizes directly."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_reference_guard.db")
    skill_dir = tmp_path / "afkbot/skills/subagent-manager"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# subagent-manager", encoding="utf-8")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-guard",
            message="Use subagent-manager and create a worker",
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"
        assert len(scripted.requests) == 1

    await engine.dispose()


async def test_explicit_skill_reference_allows_finalize_after_successful_tool_call(
    tmp_path: Path,
) -> None:
    """Final response should pass when explicitly referenced skill had successful tool execution."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_reference_ok.db")
    skill_dir = tmp_path / "afkbot/skills/subagent-manager"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# subagent-manager", encoding="utf-8")

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="subagent.profile.list",
                        params={},
                    )
                ]
            ),
            LLMResponse.final("done"),
        ]
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-ok",
            message="Use subagent-manager and create a worker",
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    await engine.dispose()


async def test_explicit_sysadmin_skill_keeps_enforceable_metadata_without_forcing_shell(
    tmp_path: Path,
) -> None:
    """Explicit executable skills should stay visible in metadata without hard finalization guards."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_explicit_sysadmin_guard.db")
    skill_dir = tmp_path / "afkbot/skills/sysadmin"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"System administration via bash.exec.\"",
                "tool_names:",
                "  - bash.exec",
                "preferred_tool_order:",
                "  - bash.exec",
                'execution_mode: "executable"',
                "---",
                "# sysadmin",
            ],
        ),
        encoding="utf-8",
    )
    scripted = MockLLMProvider([LLMResponse.final("done")])

    # Act
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-explicit-sysadmin-guard",
            message="/sysadmin установи nginx",
        )
        assert result.envelope.action == "finalize"

    # Assert
    assert result.envelope.message == "done"
    assert len(scripted.requests) == 1
    assert '"explicit_skill_requests_enforceable": ["sysadmin"]' in scripted.requests[0].context
    await engine.dispose()


async def test_explicit_sysadmin_skill_allows_finalize_after_successful_bash_exec(
    tmp_path: Path,
) -> None:
    """Explicit sysadmin invoke should count as completed after successful bash.exec."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_explicit_sysadmin_ok.db")
    skill_dir = tmp_path / "afkbot/skills/sysadmin"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"System administration via bash.exec.\"",
                "tool_names:",
                "  - bash.exec",
                "preferred_tool_order:",
                "  - bash.exec",
                'execution_mode: "executable"',
                "---",
                "# sysadmin",
            ],
        ),
        encoding="utf-8",
    )
    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [ToolCallRequest(name="bash.exec", params={"cmd": "printf 'ok'"})]
            ),
            LLMResponse.final("done"),
        ]
    )

    # Act
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=3,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-explicit-sysadmin-ok",
            message="/sysadmin установи nginx",
        )
        assert result.envelope.action == "finalize"

    # Assert
    assert result.envelope.message == "done"
    await engine.dispose()


async def test_plan_only_explicit_sysadmin_skill_is_not_enforced_without_visible_shell(
    tmp_path: Path,
) -> None:
    """Plan-only turns must not enforce explicit skills whose executable tools are hidden."""

    # Arrange
    settings, engine, factory = await create_test_db(
        tmp_path,
        "loop_llm_plan_only_explicit_sysadmin_guard.db",
    )
    skill_dir = tmp_path / "afkbot/skills/sysadmin"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"System administration via bash.exec.\"",
                "tool_names:",
                "  - bash.exec",
                "preferred_tool_order:",
                "  - bash.exec",
                'execution_mode: "executable"',
                "---",
                "# sysadmin",
            ],
        ),
        encoding="utf-8",
    )
    scripted = MockLLMProvider([LLMResponse.final("1. Inspect\n2. Execute\n3. Verify")])

    # Act
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-plan-only-explicit-sysadmin",
            message="/sysadmin установи nginx",
            context_overrides=TurnContextOverrides(planning_mode="plan_only"),
        )

    # Assert
    assert result.envelope.action == "finalize"
    assert result.envelope.message == "1. Inspect\n2. Execute\n3. Verify"
    assert len(scripted.requests) == 1
    assert scripted.requests[0].available_tools == ()
    assert '"explicit_skill_requests_enforceable": []' in scripted.requests[0].context
    await engine.dispose()


async def test_explicit_profile_skill_mention_is_reflected_in_context_same_turn(
    tmp_path: Path,
) -> None:
    """Explicit mention of a profile skill should be visible in the same-turn context."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_profile_skill_explicit_context.db")
    profile_skill = tmp_path / "profiles/default/skills/proektdok"
    profile_skill.mkdir(parents=True, exist_ok=True)
    (profile_skill / "SKILL.md").write_text(
        "# proektdok\nAnalyze docs and report critical issues.\nSecond instruction: use 5 whys.",
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-profile-skill-context",
            message="Используй proektdok и оцени docs.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    context = scripted.requests[0].context
    assert "| `proektdok` | Analyze docs and report critical issues. |" in context
    assert "Second instruction: use 5 whys." in context
    assert '"explicit_skill_requests": ["proektdok"]' in context
    assert '"explicit_skill_requests_enforceable": []' in context

    await engine.dispose()


async def test_explicit_skill_alias_is_resolved_to_canonical_name(tmp_path: Path) -> None:
    """Explicit alias mention should resolve to canonical name without forcing advisory skills."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_alias_context.db")
    telegram_skill = tmp_path / "afkbot/skills/telegram"
    telegram_skill.mkdir(parents=True, exist_ok=True)
    (telegram_skill / "SKILL.md").write_text(
        "---\naliases: telegram-send\n---\n# telegram\nUse app.run.",
        encoding="utf-8",
    )
    scripted = MockLLMProvider([LLMResponse.final("done")])

    # Act
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-alias-context",
            message="Используй telegram-send и отправь сообщение.",
        )

    # Assert
    assert result.envelope.action == "finalize"
    assert result.envelope.message == "done"
    assert len(scripted.requests) == 1
    context = scripted.requests[0].context
    assert '"explicit_skill_requests": ["telegram"]' in context
    assert '"explicit_skill_requests_enforceable": []' in context
    await engine.dispose()


async def test_explicit_subagent_mention_is_reflected_in_context_same_turn(
    tmp_path: Path,
) -> None:
    """Explicit mention of one subagent name should be reflected in runtime metadata."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_subagent_explicit_context.db")
    profile_subagent = tmp_path / "profiles/default/subagents"
    profile_subagent.mkdir(parents=True, exist_ok=True)
    (profile_subagent / "datafixer.md").write_text(
        "# datafixer\nFixes malformed datasets.",
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-subagent-context",
            message="Используй datafixer для проверки структуры данных.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    context = scripted.requests[0].context
    assert "- datafixer: datafixer" in context
    assert '"explicit_subagent_requests": ["datafixer"]' in context

    await engine.dispose()


async def test_explicit_skill_request_blocks_subagent_substitution(tmp_path: Path) -> None:
    """When only skill is explicitly requested, subagent.run substitution must be blocked."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_subagent_substitution_guard.db")
    profile_skill = tmp_path / "profiles/default/skills/proektdok"
    profile_skill.mkdir(parents=True, exist_ok=True)
    (profile_skill / "SKILL.md").write_text("# proektdok\nGive concise product analysis.", encoding="utf-8")

    scripted = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [ToolCallRequest(name="subagent.run", params={"prompt": "analyze", "subagent_name": "proektdok"})]
            ),
            LLMResponse.final("done"),
        ]
    )
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-subagent-substitution-guard",
            message="Используй скилл proektdok и дай оценку.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        tool_results = [json.loads(item.payload_json) for item in events if item.event_type == "tool.result"]
        assert tool_results
        assert tool_results[0]["name"] == "subagent.run"
        assert tool_results[0]["result"]["ok"] is False
        assert tool_results[0]["result"]["error_code"] == "subagent_intent_mismatch"

    await engine.dispose()


async def test_security_secrets_mention_is_not_enforced_as_executable_skill(tmp_path: Path) -> None:
    """Mentioning non-executable synthetic skill must not force execution guard."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_security_secrets_mention.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-security-secrets",
            message="Use security-secrets and continue",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    await engine.dispose()


async def test_hidden_skill_mention_is_not_enforced_when_tool_is_not_executable(
    tmp_path: Path,
) -> None:
    """Skill mention should not trigger guard when policy hides all tools for that skill."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_hidden_skill_mention.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    # Act
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        row = await ProfilePolicyRepository(session).get_or_create_default("default")
        row.policy_enabled = True
        row.allowed_tools_json = json.dumps(["debug.echo"], ensure_ascii=True, sort_keys=True)
        await session.flush()

        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=2,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-hidden-skill",
            message="Use subagent-manager and continue",
        )

        # Assert
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    await engine.dispose()
