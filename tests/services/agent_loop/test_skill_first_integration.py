"""Integration tests for implicit skill-first routing and telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.runlog_event import RunlogEvent
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.llm import LLMResponse, MockLLMProvider, ToolCallRequest
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


class _NoopToolParams(ToolParameters):
    app_name: str | None = None
    action: str | None = None
    params: dict[str, object] = Field(default_factory=dict)


class _CredentialsListStub(ToolBase):
    name = "credentials.list"
    description = "credentials list stub"
    parameters_model = _NoopToolParams

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        _ = ctx, params
        return ToolResult(ok=True, payload={"items": []})


class _CredentialsRequestStub(ToolBase):
    name = "credentials.request"
    description = "credentials request stub"
    parameters_model = _NoopToolParams

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        _ = ctx, params
        return ToolResult(ok=True, payload={})


class _QueuedAppRunStub(ToolBase):
    name = "app.run"
    description = "app run stub"
    parameters_model = _NoopToolParams

    def __init__(self, *results: ToolResult) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        _ = ctx
        self.calls.append(params.model_dump())
        if self._results:
            return self._results.pop(0)
        return ToolResult(ok=True, payload={"ok": True})


def _tool_registry_with_app_results(*results: ToolResult) -> ToolRegistry:
    return ToolRegistry(
        [
            _CredentialsListStub(),
            _CredentialsRequestStub(),
            _QueuedAppRunStub(*results),
        ]
    )


async def _prepare_db(
    tmp_path: Path,
    db_name: str,
) -> tuple[Settings, AsyncEngine, async_sessionmaker[AsyncSession]]:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    security_skill_dir = tmp_path / "afkbot/skills/security-secrets"
    telegram_skill_dir = tmp_path / "afkbot/skills/telegram"
    bootstrap_dir.mkdir(parents=True)
    security_skill_dir.mkdir(parents=True)
    telegram_skill_dir.mkdir(parents=True)

    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    (security_skill_dir / "SKILL.md").write_text("# security-secrets", encoding="utf-8")
    (telegram_skill_dir / "SKILL.md").write_text(
        """---
name: telegram
description: "Telegram Bot API operations."
triggers:
  - telegram
  - телеграм
  - отправь в телеграм
tool_names:
  - credentials.list
  - credentials.request
  - app.run
app_names:
  - telegram
preferred_tool_order:
  - credentials.list
  - credentials.request
  - app.run
---
# telegram

Use `app.run` with `app_name=telegram`.
""",
        encoding="utf-8",
    )

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    return settings, engine, create_session_factory(engine)


async def test_implicit_skill_first_filters_tools_and_logs_route_metadata(tmp_path: Path) -> None:
    """Implicit Telegram intent should route to telegram skill and expose only its tools."""

    settings, engine, factory = await _prepare_db(tmp_path, "skill_first_route.db")
    provider = MockLLMProvider([LLMResponse.final("telegram ready")])

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=_tool_registry_with_app_results(),
            llm_provider=provider,
            llm_max_iterations=1,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-implicit-route",
            message="отправь в телеграм",
        )

        assert result.envelope.action == "finalize"
        assert provider.requests
        assert [tool.name for tool in provider.requests[0].available_tools] == [
            "credentials.list",
            "credentials.request",
            "app.run",
        ]

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        plan_payload = json.loads([event for event in events if event.event_type == "turn.plan"][0].payload_json)
        assert plan_payload["selected_skill_names"] == ["telegram"]
        assert plan_payload["inferred_skill_names"] == ["telegram"]
        assert plan_payload["explicit_skill_mentions"] == []
        assert plan_payload["available_tools_after_filter"] == [
            "credentials.list",
            "credentials.request",
            "app.run",
        ]

        llm_start_payload = json.loads(
            [event for event in events if event.event_type == "llm.call.start"][0].payload_json
        )
        assert llm_start_payload["available_tool_names"] == [
            "credentials.list",
            "credentials.request",
            "app.run",
        ]

    await engine.dispose()


async def test_implicit_skill_first_missing_credentials_returns_secure_envelope(
    tmp_path: Path,
) -> None:
    """Implicit Telegram flow should convert missing credentials into secure handoff."""

    settings, engine, factory = await _prepare_db(tmp_path, "skill_first_missing_creds.db")
    provider = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="app.run",
                        params={
                            "app_name": "telegram",
                            "action": "send_message",
                            "params": {"text": "hi"},
                        },
                        call_id="call_telegram_1",
                    )
                ]
            )
        ]
    )
    tool_registry = _tool_registry_with_app_results(
        ToolResult.error(
            error_code="credentials_missing",
            reason="telegram token is missing",
            metadata={
                "integration_name": "telegram",
                "credential_name": "telegram_token",
                "credential_profile_key": "default",
            },
        )
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=tool_registry,
            llm_provider=provider,
            llm_max_iterations=2,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-implicit-missing-creds",
            message="отправь в телеграм привет",
        )

        assert result.envelope.action == "request_secure_field"
        assert result.envelope.secure_field == "telegram_token"
        assert result.envelope.spec_patch is not None
        assert result.envelope.spec_patch["tool_name"] == "app.run"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert any(event.event_type == "skill.read" for event in events)
        assert any(event.event_type == "turn.request_secure_field" for event in events)

    await engine.dispose()


async def test_implicit_skill_first_successful_app_run_finishes_turn(tmp_path: Path) -> None:
    """Implicit Telegram flow should execute app.run and finalize after provider response."""

    settings, engine, factory = await _prepare_db(tmp_path, "skill_first_success.db")
    provider = MockLLMProvider(
        [
            LLMResponse.tool_calls_response(
                [
                    ToolCallRequest(
                        name="app.run",
                        params={
                            "app_name": "telegram",
                            "action": "get_me",
                            "params": {},
                        },
                        call_id="call_telegram_ok",
                    )
                ]
            ),
            LLMResponse.final("telegram ok"),
        ]
    )
    tool_registry = _tool_registry_with_app_results(
        ToolResult(ok=True, payload={"user": {"id": 1, "username": "bot"}})
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=tool_registry,
            llm_provider=provider,
            llm_max_iterations=2,
        )
        result = await loop.run_turn(
            profile_id="default",
            session_id="s-implicit-success",
            message="проверь телеграм бота",
        )

        assert result.envelope.action == "finalize"
        assert result.envelope.message == "telegram ok"
        assert len(provider.requests) == 2
        second_history = provider.requests[1].history
        assert any(message.role == "tool" and message.tool_name == "app.run" for message in second_history)

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert any(event.event_type == "skill.read" for event in events)
        assert any(event.event_type == "tool.result" for event in events)
        assert any(event.event_type == "turn.finalize" for event in events)

    await engine.dispose()
