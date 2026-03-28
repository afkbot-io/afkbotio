"""AgentLoop tests for routed skill surfaces and prompt context shaping."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.db.session import session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.llm import LLMResponse, MockLLMProvider
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import create_test_db


async def test_llm_filters_tools_to_routed_skill_surface(tmp_path: Path) -> None:
    """Skill routing should restrict LLM-visible tools to the selected skill surface."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_routed_skill_tools.db")
    telegram_skill = tmp_path / "afkbot/skills/telegram"
    telegram_skill.mkdir(parents=True, exist_ok=True)
    (telegram_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Telegram integration via app.run.\"",
                "triggers:",
                "  - телеграм",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - telegram",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# telegram",
                "Use app.run.",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-routed-skill-tools",
            message="Отправь сообщение в телеграм.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    request = scripted.requests[0]
    tool_map = {tool.name: tool for tool in request.available_tools}
    assert set(tool_map) == {"app.run", "credentials.list", "credentials.request"}
    assert "# Selected Skill Cards" in request.context
    assert "## telegram" in request.context
    assert "- execution_mode: executable" in request.context
    assert "- tools: credentials.list, credentials.request, app.run" in request.context

    app_schema = tool_map["app.run"].parameters_schema
    properties = app_schema.get("properties")
    assert isinstance(properties, dict)
    app_prop = properties.get("app_name")
    assert isinstance(app_prop, dict)
    assert app_prop.get("const") == "telegram"
    assert "skill_name" not in properties

    await engine.dispose()


async def test_llm_routes_explicit_slash_skill_invoke_to_skill_surface(tmp_path: Path) -> None:
    """Explicit slash skill invoke should route even without a natural-language trigger match."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_explicit_slash_skill.db")
    imap_skill = tmp_path / "afkbot/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - imap",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# imap",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-explicit-slash-skill",
            message="/imap получи список писем",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"credentials.list", "credentials.request", "app.run"}
    assert '"explicit_skill_requests": ["imap"]' in scripted.requests[0].context
    assert '"selected_skill_requests": ["imap"]' in scripted.requests[0].context
    await engine.dispose()


async def test_llm_routes_explicit_dollar_skill_invoke_to_same_surface(tmp_path: Path) -> None:
    """Explicit dollar skill invoke should route to the same routed surface as slash invoke."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_explicit_dollar_skill.db")
    imap_skill = tmp_path / "afkbot/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - imap",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# imap",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-explicit-dollar-skill",
            message="$imap получи список писем",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"credentials.list", "credentials.request", "app.run"}
    assert '"explicit_skill_requests": ["imap"]' in scripted.requests[0].context
    assert '"selected_skill_requests": ["imap"]' in scripted.requests[0].context
    await engine.dispose()


async def test_llm_ignores_descriptive_platform_mentions_for_implicit_skill_routing(
    tmp_path: Path,
) -> None:
    """Descriptive platform text should not narrow tools through generic implicit trigger words."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_descriptive_platform_mentions.db")
    automation_skill = tmp_path / "afkbot/skills/automation"
    automation_skill.mkdir(parents=True, exist_ok=True)
    (automation_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Automation management.\"",
                "triggers:",
                "  - webhook",
                "tool_names:",
                "  - automation.create",
                'execution_mode: "executable"',
                "---",
                "# automation",
            ],
        ),
        encoding="utf-8",
    )
    browser_skill = tmp_path / "afkbot/skills/browser-control"
    browser_skill.mkdir(parents=True, exist_ok=True)
    (browser_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Browser automation.\"",
                "triggers:",
                "  - browser",
                "tool_names:",
                "  - browser.control",
                'execution_mode: "executable"',
                "---",
                "# browser-control",
            ],
        ),
        encoding="utf-8",
    )
    telegram_skill = tmp_path / "afkbot/skills/telegram"
    telegram_skill.mkdir(parents=True, exist_ok=True)
    (telegram_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Telegram integration.\"",
                "triggers:",
                "  - telegram",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - telegram",
                'execution_mode: "executable"',
                "---",
                "# telegram",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-descriptive-platform-mentions",
            message=(
                "AFKBOT — это локально разворачиваемая платформа для запуска AI-агентов, "
                "где CLI поддерживает planning-режима и инструментальное выполнение. "
                "Также есть browser, Telegram Bot API и webhook автоматизации."
            ),
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert "debug.echo" in names
    assert "browser.control" in names
    assert "app.run" in names
    assert '"selected_skill_requests": [' not in scripted.requests[0].context
    await engine.dispose()


async def test_llm_does_not_route_agent_cli_skill_for_docs_style_question(tmp_path: Path) -> None:
    """Docs-style questions about a CLI should not narrow the tool surface to a bash skill."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_agent_cli_docs_question.db")
    codex_skill = tmp_path / "afkbot/skills/codex-cli"
    codex_skill.mkdir(parents=True, exist_ok=True)
    (codex_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Codex CLI orchestration via bash.exec."',
                "triggers:",
                "  - codex cli",
                "  - codex exec",
                "  - codex review",
                "tool_names:",
                "  - bash.exec",
                'execution_mode: "executable"',
                "---",
                "# codex-cli",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-agent-cli-docs-question",
            message="How do I use codex with AFKBOT?",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert "bash.exec" in names
    assert "app.run" in names
    assert '"selected_skill_requests": ["codex-cli"]' not in scripted.requests[0].context
    assert '"explicit_skill_requests": ["codex-cli"]' not in scripted.requests[0].context
    await engine.dispose()


async def test_llm_routes_explicit_skill_near_match_to_unique_surface(tmp_path: Path) -> None:
    """Minor near-miss explicit skill invokes should resolve to one unique skill."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_explicit_skill_near_match.db")
    gh_skill = tmp_path / "profiles/default/skills/gh-address-comments"
    gh_skill.mkdir(parents=True, exist_ok=True)
    (gh_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Address GitHub PR review comments with gh CLI.\"",
                "tool_names:",
                "  - bash.exec",
                "preferred_tool_order:",
                "  - bash.exec",
                'execution_mode: "executable"',
                "---",
                "# gh-address-comments",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-explicit-skill-near-match",
            message="@gh-address-comment address PR feedback",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"bash.exec"}
    assert '"explicit_skill_requests": ["gh-address-comments"]' in scripted.requests[0].context
    assert '"selected_skill_requests": ["gh-address-comments"]' in scripted.requests[0].context
    await engine.dispose()


async def test_llm_routes_marketplace_requests_to_skill_creator_surface(tmp_path: Path) -> None:
    """Marketplace skill requests should expose marketplace/profile skill tools."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_marketplace_surface.db")
    skill_creator = tmp_path / "afkbot/skills/skill-creator"
    skill_creator.mkdir(parents=True, exist_ok=True)
    (skill_creator / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Manage profile skills and skill marketplace installs.\"",
                "triggers:",
                "  - маркетплейс скиллов",
                "  - маркет плейс скиллов",
                "tool_names:",
                "  - skill.profile.list",
                "  - skill.profile.get",
                "  - skill.profile.upsert",
                "  - skill.profile.delete",
                "  - skill.marketplace.list",
                "  - skill.marketplace.install",
                "preferred_tool_order:",
                "  - skill.profile.list",
                "  - skill.marketplace.list",
                "  - skill.marketplace.install",
                "---",
                "# skill-creator",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-marketplace-skill-surface",
            message="покажи маркетплейс скиллов",
        )
    assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert {"skill.profile.list", "skill.marketplace.list", "skill.marketplace.install"} <= names
    await engine.dispose()


async def test_llm_routes_package_requests_to_sysadmin_shell_surface(tmp_path: Path) -> None:
    """Package and service phrasing should route to sysadmin and expose only bash.exec."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_sysadmin_surface.db")
    sysadmin_skill = tmp_path / "afkbot/skills/sysadmin"
    sysadmin_skill.mkdir(parents=True, exist_ok=True)
    (sysadmin_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"System administration via bash.exec for packages and services.\"",
                "triggers:",
                "  - install nginx",
                "  - apt update",
                "  - обнови пакеты",
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
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-sysadmin-surface",
            message="установи nginx, обнови пакеты через apt",
        )
        assert result.envelope.action == "finalize"

    # Assert
    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"bash.exec"}
    assert "# Selected Skill Cards" in scripted.requests[0].context
    assert "## sysadmin" in scripted.requests[0].context
    await engine.dispose()


async def test_llm_routes_spaced_marketplace_requests_to_skill_creator_surface(tmp_path: Path) -> None:
    """Spaced Russian marketplace phrasing should still route to skill creator."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_spaced_marketplace_surface.db")
    skill_creator = tmp_path / "afkbot/skills/skill-creator"
    skill_creator.mkdir(parents=True, exist_ok=True)
    (skill_creator / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Manage profile skills and marketplace installs.\"",
                "triggers:",
                "  - маркет плейс скиллов",
                "tool_names:",
                "  - skill.profile.list",
                "  - skill.marketplace.list",
                "  - skill.marketplace.install",
                "---",
                "# skill-creator",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-marketplace-skill-surface-spaced",
            message="покажи маркет плейс скиллов",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert {"skill.profile.list", "skill.marketplace.list", "skill.marketplace.install"} <= names
    await engine.dispose()


async def test_llm_routes_imap_mail_requests_to_imap_surface(tmp_path: Path) -> None:
    """Mailbox listing requests should expose the IMAP secure integration surface."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_imap_skill_surface.db")
    imap_skill = tmp_path / "afkbot/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "triggers:",
                "  - список писем с почты",
                "  - через imap",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - imap",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# imap",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-imap-skill-surface",
            message="получи список писем с почты",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"credentials.list", "credentials.request", "app.run"}
    await engine.dispose()


async def test_llm_routes_imap_followup_phrase_to_imap_surface(tmp_path: Path) -> None:
    """Short follow-up phrasing like 'через imap' should still route to IMAP tools."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_imap_followup_skill_surface.db")
    imap_skill = tmp_path / "afkbot/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "triggers:",
                "  - через imap",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - imap",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# imap",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-imap-skill-surface-followup",
            message="через imap",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert names == {"credentials.list", "credentials.request", "app.run"}
    await engine.dispose()


async def test_llm_routes_automation_plus_telegram_requests_to_combined_surface(tmp_path: Path) -> None:
    """Automation requests that mention Telegram should expose automation and Telegram tools."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_automation_telegram_surface.db")
    automation_skill = tmp_path / "afkbot/skills/automation"
    automation_skill.mkdir(parents=True, exist_ok=True)
    (automation_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Automation management via automation tools."',
                "triggers:",
                "  - cron",
                "  - создай крон",
                "tool_names:",
                "  - automation.create",
                "  - automation.get",
                "  - automation.list",
                "  - automation.update",
                "  - automation.delete",
                'execution_mode: "executable"',
                "preferred_tool_order:",
                "  - automation.create",
                "---",
                "# automation",
            ],
        ),
        encoding="utf-8",
    )
    telegram_skill = tmp_path / "afkbot/skills/telegram"
    telegram_skill.mkdir(parents=True, exist_ok=True)
    (telegram_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Telegram integration via app.run."',
                "triggers:",
                "  - телеграм",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - telegram",
                'execution_mode: "executable"',
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# telegram",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-automation-telegram-surface",
            message="Создай крон который раз в 5 минут отправляет через телеграм сообщение ПРИВЕТ",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert {"automation.create", "credentials.list", "credentials.request", "app.run"} <= names
    await engine.dispose()


async def test_llm_keeps_automation_surface_for_confirmation_followup(tmp_path: Path) -> None:
    """Short confirmations should keep automation tools when the prior turn selected automation."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_automation_followup_surface.db")
    automation_skill = tmp_path / "afkbot/skills/automation"
    automation_skill.mkdir(parents=True, exist_ok=True)
    (automation_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Automation management via automation tools."',
                "triggers:",
                "  - cron",
                "  - создай крон",
                "tool_names:",
                "  - automation.create",
                "  - automation.get",
                "  - automation.list",
                'execution_mode: "executable"',
                "preferred_tool_order:",
                "  - automation.create",
                "---",
                "# automation",
            ],
        ),
        encoding="utf-8",
    )
    scripted = MockLLMProvider([LLMResponse.final("first"), LLMResponse.final("second")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        first = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-automation-affinity",
            message="Создай крон на каждые 5 минут",
        )
        second = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-automation-affinity",
            message="Да",
        )
        assert first.envelope.action == "finalize"
        assert second.envelope.action == "finalize"

    assert len(scripted.requests) == 2
    second_names = {tool.name for tool in scripted.requests[1].available_tools}
    assert "automation.create" in second_names
    assert '"affinity_skill_requests": ["automation"]' in scripted.requests[1].context
    await engine.dispose()


async def test_llm_keeps_skill_affinity_for_short_followup_turn(tmp_path: Path) -> None:
    """A short follow-up like 'ещё' should reuse the previous selected skill in-session."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_skill_affinity.db")
    imap_skill = tmp_path / "afkbot/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "tool_names:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "app_names:",
                "  - imap",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - credentials.request",
                "  - app.run",
                "---",
                "# imap",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done-1"), LLMResponse.final("done-2")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        first = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-affinity",
            message="/imap получи список писем",
        )
        second = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-skill-affinity",
            message="ещё",
        )
        assert first.envelope.action == "finalize"
        assert second.envelope.action == "finalize"

    assert len(scripted.requests) == 2
    names = {tool.name for tool in scripted.requests[1].available_tools}
    assert names == {"credentials.list", "credentials.request", "app.run"}
    assert '"affinity_skill_requests": ["imap"]' in scripted.requests[1].context
    await engine.dispose()


async def test_automation_runtime_keeps_broad_tool_surface_with_inferred_skills(tmp_path: Path) -> None:
    """Automation-triggered turns should keep broad tools while preferring selected automation skills."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_automation_runtime_broad_surface.db")
    automation_skill = tmp_path / "afkbot/skills/automation"
    automation_skill.mkdir(parents=True, exist_ok=True)
    (automation_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Automation management via automation tools."',
                "triggers:",
                "  - telegram",
                "tool_names:",
                "  - automation.create",
                "  - app.run",
                "  - credentials.list",
                "preferred_tool_order:",
                "  - automation.create",
                "  - app.run",
                'execution_mode: "executable"',
                "---",
                "# automation",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-automation-runtime-broad-surface",
            message="Отправь ПРИВЕТ в Telegram",
            context_overrides=TurnContextOverrides(runtime_metadata={"transport": "automation"}),
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    request = scripted.requests[0]
    names = {tool.name for tool in request.available_tools}
    assert "app.run" in names
    assert "bash.exec" in names
    assert "http.request" in names
    await engine.dispose()


async def test_llm_fail_closes_when_explicit_skill_has_no_executable_surface(tmp_path: Path) -> None:
    """Explicit advisory-only skill invoke must not reopen the full tool catalog."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_fail_closed_skill_tools.db")
    empty_skill = tmp_path / "afkbot/skills/empty-skill"
    empty_skill.mkdir(parents=True, exist_ok=True)
    (empty_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Matches a special phrase but exposes no tools.\"",
                "triggers:",
                "  - only-skill-mode",
                "---",
                "# empty-skill",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-fail-closed-skill-tools",
            message="@empty-skill please help now.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    assert scripted.requests[0].available_tools != ()
    assert '"explicit_skill_requests": ["empty-skill"]' in scripted.requests[0].context
    assert "The explicit selection is advisory-only." in scripted.requests[0].context

    await engine.dispose()


async def test_llm_fail_closes_when_explicit_skill_is_unavailable(tmp_path: Path) -> None:
    """Explicit unavailable skill invoke must not fall back to generic tools."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_fail_closed_unavailable_skill.db")
    doc_skill = tmp_path / "profiles/default/skills/doc"
    doc_skill.mkdir(parents=True, exist_ok=True)
    (doc_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Create and edit .docx documents.\"",
                "aliases:",
                "  - docx",
                "tool_names:",
                "  - file.read",
                "  - file.write",
                "requires_python_packages:",
                "  - definitely-missing-docx-package",
                "---",
                "# doc",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-fail-closed-unavailable-skill",
            message="@doc создай docx с текстом ПРИВЕТ",
        )
        assert result.envelope.action == "finalize"
        assert "doc" in result.envelope.message
        assert "python:definitely-missing-docx-package" in result.envelope.message
        assert "uv pip install definitely-missing-docx-package" in result.envelope.message
        assert "afk skill normalize --profile default doc" in result.envelope.message

    assert len(scripted.requests) == 0

    await engine.dispose()


async def test_llm_hides_app_run_when_skill_declares_it_without_allowed_app_mapping(
    tmp_path: Path,
) -> None:
    """app.run must stay hidden when a routed skill does not map to any allowed app."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_hide_unmapped_app_run.db")
    strange_skill = tmp_path / "afkbot/skills/strange-app-skill"
    strange_skill.mkdir(parents=True, exist_ok=True)
    (strange_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"References app.run but has no app mapping.\"",
                "triggers:",
                "  - strange-app-mode",
                "tool_names:",
                "  - app.run",
                "---",
                "# strange-app-skill",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-hide-unmapped-app-run",
            message="strange-app-mode please",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert "app.run" not in names

    await engine.dispose()


async def test_llm_hides_app_run_when_only_unavailable_skill_maps_to_app(
    tmp_path: Path,
) -> None:
    """Unavailable selected skills must not contribute app routing to executable surface."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_hide_unavailable_app_run.db")
    helper_skill = tmp_path / "afkbot/skills/helper-skill"
    helper_skill.mkdir(parents=True, exist_ok=True)
    (helper_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Simple helper skill.\"",
                "triggers:",
                "  - helper-mode",
                "tool_names:",
                "  - file.read",
                'execution_mode: "executable"',
                "---",
                "# helper-skill",
            ],
        ),
        encoding="utf-8",
    )
    imap_skill = tmp_path / "profiles/default/skills/imap"
    imap_skill.mkdir(parents=True, exist_ok=True)
    (imap_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Mailbox search via IMAP.\"",
                "tool_names:",
                "  - app.run",
                "requires_python_packages:",
                "  - definitely-missing-imap-package",
                'execution_mode: "executable"',
                "---",
                "# imap",
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
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-hide-unavailable-app-run",
            message="@imap helper-mode",
        )
        assert result.envelope.action == "finalize"

    # Assert
    assert result.envelope.message == "done"
    assert len(scripted.requests) == 1
    assert '"explicit_skill_requests_enforceable": []' in scripted.requests[0].context
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert "file.read" in names
    assert "app.run" not in names
    await engine.dispose()


async def test_llm_combines_file_ops_and_diffs_for_file_change_requests(tmp_path: Path) -> None:
    """File change requests asking for diffs should expose file mutation tools and diffs.render."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_file_diffs_surface.db")
    file_ops_skill = tmp_path / "afkbot/skills/file-ops"
    file_ops_skill.mkdir(parents=True, exist_ok=True)
    (file_ops_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Workspace file operations.\"",
                "triggers:",
                "  - создай файл",
                "  - отредактируй файл",
                "tool_names:",
                "  - file.list",
                "  - file.read",
                "  - file.write",
                "  - file.edit",
                "  - file.search",
                "  - diffs.render",
                "preferred_tool_order:",
                "  - file.write",
                "  - file.edit",
                "  - diffs.render",
                "---",
                "# file-ops",
            ],
        ),
        encoding="utf-8",
    )
    diffs_skill = tmp_path / "afkbot/skills/diffs"
    diffs_skill.mkdir(parents=True, exist_ok=True)
    (diffs_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Render diffs.\"",
                "triggers:",
                "  - diffs",
                "tool_names:",
                "  - diffs.render",
                "  - file.read",
                "---",
                "# diffs",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-file-diffs-surface",
            message="создай файл и верни DIFFS",
        )
        assert result.envelope.action == "finalize"

    assert len(scripted.requests) == 1
    names = {tool.name for tool in scripted.requests[0].available_tools}
    assert "file.write" in names
    assert "file.edit" in names
    assert "diffs.render" in names
    await engine.dispose()


async def test_skill_trigger_matching_is_unicode_token_aware(tmp_path: Path) -> None:
    """Russian trigger should not match as a substring inside another word."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_unicode_skill_trigger.db")
    telegram_skill = tmp_path / "afkbot/skills/telegram"
    telegram_skill.mkdir(parents=True, exist_ok=True)
    (telegram_skill / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "description: \"Telegram integration.\"",
                "triggers:",
                "  - телеграм",
                "tool_names:",
                "  - app.run",
                "app_names:",
                "  - telegram",
                "---",
                "# telegram",
            ],
        ),
        encoding="utf-8",
    )

    scripted = MockLLMProvider([LLMResponse.final("done")])
    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry.from_settings(settings),
            llm_provider=scripted,
            llm_max_iterations=1,
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-unicode-skill-trigger",
            message="Это слово мегателеграмма, а не отдельный триггер.",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    assert '"selected_skill_requests": ["telegram"]' not in scripted.requests[0].context

    await engine.dispose()


async def test_policy_safety_block_is_injected_into_llm_context(tmp_path: Path) -> None:
    """LLM system context should include runtime safety policy block and metadata fields."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_policy_safety_context.db")
    scripted = MockLLMProvider([LLMResponse.final("done")])

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.policy_preset = "strict"
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
            session_id="s-llm-policy-safety-context",
            message="inspect",
        )
        assert result.envelope.action == "finalize"
        assert result.envelope.message == "done"

    assert len(scripted.requests) == 1
    context = scripted.requests[0].context
    assert "# Runtime Safety Policy" in context
    assert "Preset: strict." in context
    assert '"policy_preset": "strict"' in context
    assert '"safety_confirmation_mode": "confirm_all_critical_ops"' in context

    await engine.dispose()


async def test_new_profile_skill_is_visible_without_new_session(tmp_path: Path) -> None:
    """Newly added profile skill should appear in context on next turn in same session."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_llm_profile_skill_hot_reload.db")
    scripted = MockLLMProvider([LLMResponse.final("first"), LLMResponse.final("second")])

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
        first = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-profile-skill-hot",
            message="Привет",
        )
        assert first.envelope.action == "finalize"

        new_skill = tmp_path / "profiles/default/skills/proektdok"
        new_skill.mkdir(parents=True, exist_ok=True)
        (new_skill / "SKILL.md").write_text("# proektdok\nUse productologist approach.", encoding="utf-8")

        second = await loop.run_turn(
            profile_id="default",
            session_id="s-llm-profile-skill-hot",
            message="Используй proektdok для анализа.",
        )
        assert second.envelope.action == "finalize"
        assert second.envelope.message == "second"

    assert len(scripted.requests) == 2
    second_context = scripted.requests[1].context
    assert "| `proektdok` | Use productologist approach. |" in second_context
    assert '"explicit_skill_requests": ["proektdok"]' in second_context

    await engine.dispose()
