"""Tests for subagent tool plugins contract."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest
from pytest import MonkeyPatch

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.subagents.runner import SubagentExecutionResult, SubagentRunner
from afkbot.services.subagents.service import SubagentService
from afkbot.services.subagents import reset_subagent_services_async
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.subagent_result import create_tool as create_subagent_result_tool
from afkbot.services.tools.plugins.subagent_run import create_tool as create_subagent_run_tool
from afkbot.services.tools.plugins.subagent_wait import create_tool as create_subagent_wait_tool
from afkbot.settings import get_settings


def _prepare_environment(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    core_path = tmp_path / "afkbot/subagents/researcher.md"
    core_path.parent.mkdir(parents=True)
    core_path.write_text("# researcher", encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'subagent_plugins.db'}")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _cleanup_subagent_services() -> AsyncIterator[None]:
    await reset_subagent_services_async()
    yield
    await reset_subagent_services_async()


async def test_subagent_run_wait_result_plugins_roundtrip(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Plugins should provide a complete run/wait/result lifecycle."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    class _FakeRunner(SubagentRunner):
        async def execute(
            self,
            *,
            session,
            task_id: str,
            profile_id: str,
            parent_session_id: str,
            subagent_name: str,
            subagent_markdown: str,
            prompt: str,
        ) -> SubagentExecutionResult:
            _ = session, profile_id, parent_session_id, subagent_markdown
            return SubagentExecutionResult(
                output=f"{subagent_name}:{prompt}",
                child_session_id=f"child:{task_id}",
                child_run_id=7,
            )

    service = SubagentService(
        settings=settings,
        runner=_FakeRunner(settings),
        launch_mode="inline",
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.subagent_run.plugin.get_subagent_service",
        lambda settings: service,
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.subagent_wait.plugin.get_subagent_service",
        lambda settings: service,
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.subagent_result.plugin.get_subagent_service",
        lambda settings: service,
    )

    run_tool = create_subagent_run_tool(settings)
    wait_tool = create_subagent_wait_tool(settings)
    result_tool = create_subagent_result_tool(settings)

    run_params = run_tool.parse_params(
        {"prompt": "hello", "subagent_name": "researcher"},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    run_result = await run_tool.execute(ctx, run_params)
    assert run_result.ok is True
    assert run_result.payload["timeout_sec"] == 900
    task_id = str(run_result.payload["task_id"])

    wait_params = wait_tool.parse_params(
        {"task_id": task_id, "timeout_sec": 2},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    wait_result = await wait_tool.execute(ctx, wait_params)
    assert wait_result.ok is True

    final_result = None
    for _ in range(25):
        result_params = result_tool.parse_params(
            {"task_id": task_id},
            default_timeout_sec=15,
            max_timeout_sec=900,
        )
        final_result = await result_tool.execute(ctx, result_params)
        if final_result.payload["status"] != "running":
            break
        await asyncio.sleep(0.2)

    assert final_result is not None
    assert final_result.ok is True
    assert final_result.payload["status"] == "completed"
    await service.shutdown()


async def test_subagent_run_accepts_long_timeout_with_subagent_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """subagent.run should use 900s subagent timeout policy, not global tool timeout."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    run_tool = create_subagent_run_tool(settings)

    params = run_tool.parse_params(
        {"prompt": "hello", "timeout_sec": 600},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    result = await run_tool.execute(ctx, params)
    assert result.ok is True
    assert result.payload["timeout_sec"] == 600


def test_subagent_wait_uses_wait_defaults(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """subagent.wait should use wait-specific timeout defaults, not run timeout defaults."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    wait_tool = create_subagent_wait_tool(settings)

    params = wait_tool.parse_params(
        {"task_id": "t-1"},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    assert params.timeout_sec == settings.subagent_wait_default_sec


async def test_subagent_plugins_handle_missing_task(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Wait/result plugins should return deterministic not-found errors."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    wait_tool = create_subagent_wait_tool(settings)
    result_tool = create_subagent_result_tool(settings)

    wait_params = wait_tool.parse_params(
        {"task_id": "missing-task"},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    wait_result = await wait_tool.execute(ctx, wait_params)
    assert wait_result.ok is False
    assert wait_result.error_code == "subagent_task_not_found"

    result_params = result_tool.parse_params(
        {"task_id": "missing-task"},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    result = await result_tool.execute(ctx, result_params)
    assert result.ok is False
    assert result.error_code == "subagent_task_not_found"


async def test_subagent_plugins_enforce_task_ownership(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """wait/result should not expose another profile task by task_id."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    run_tool = create_subagent_run_tool(settings)
    wait_tool = create_subagent_wait_tool(settings)
    result_tool = create_subagent_result_tool(settings)

    owner_ctx = ToolContext(profile_id="p1", session_id="s-owner", run_id=1)
    other_ctx = ToolContext(profile_id="p2", session_id="s-other", run_id=2)

    run_params = run_tool.parse_params(
        {"prompt": "hello"},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    created = await run_tool.execute(owner_ctx, run_params)
    assert created.ok is True
    task_id = str(created.payload["task_id"])

    wait_params = wait_tool.parse_params(
        {"task_id": task_id},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    wait = await wait_tool.execute(other_ctx, wait_params)
    assert wait.ok is False
    assert wait.error_code == "subagent_task_not_found"

    result_params = result_tool.parse_params(
        {"task_id": task_id},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    outcome = await result_tool.execute(other_ctx, result_params)
    assert outcome.ok is False
    assert outcome.error_code == "subagent_task_not_found"


async def test_subagent_plugins_enforce_session_ownership_with_same_profile(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Same profile but different session must not access task result."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    run_tool = create_subagent_run_tool(settings)
    wait_tool = create_subagent_wait_tool(settings)

    owner_ctx = ToolContext(profile_id="default", session_id="session-a", run_id=1)
    other_session_ctx = ToolContext(profile_id="default", session_id="session-b", run_id=2)

    run_params = run_tool.parse_params(
        {"prompt": "hello"},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    created = await run_tool.execute(owner_ctx, run_params)
    task_id = str(created.payload["task_id"])

    wait_params = wait_tool.parse_params(
        {"task_id": task_id},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    wait = await wait_tool.execute(other_session_ctx, wait_params)
    assert wait.ok is False
    assert wait.error_code == "subagent_task_not_found"


async def test_subagent_run_plugin_respects_profile_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """subagent.run should return policy violation when profile blocks this tool."""

    _prepare_environment(tmp_path, monkeypatch)
    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.allowed_tools_json = '["debug.echo"]'
        await session.flush()
    await engine.dispose()

    run_tool = create_subagent_run_tool(settings)
    params = run_tool.parse_params(
        {"prompt": "hello"},
        default_timeout_sec=15,
        max_timeout_sec=120,
    )
    result = await run_tool.execute(
        ToolContext(profile_id="default", session_id="s-1", run_id=1),
        params,
    )
    assert result.ok is False
    assert result.error_code == "profile_policy_violation"
