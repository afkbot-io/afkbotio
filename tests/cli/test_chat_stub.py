"""Tests for chat command internals."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands.chat_planning import build_plan_only_overrides
from afkbot.cli.commands.chat_target import build_cli_runtime_overrides
from afkbot.cli.main import app
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.runtime_config import ProfileRuntimeConfigService
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing.runtime_target import RuntimeTarget
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.chat_session.interrupts import run_turn_interruptibly
from afkbot.services.agent_loop.turn_runtime import run_once_result
from afkbot.settings import get_settings
from tests.cli._rendering import invoke_plain_help, strip_ansi


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    skills_dir = tmp_path / "afkbot/skills/security-secrets"
    bootstrap_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    (skills_dir / "SKILL.md").write_text("# security-secrets", encoding="utf-8")

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("AFKBOT_LLM_PROVIDER", "openrouter")
    get_settings.cache_clear()


async def test_chat_stub_runs_single_turn(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat command handler should return one deterministic typed envelope."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)

    # Act
    result = await run_once_result(message="hello", profile_id="default", session_id="s")

    # Assert
    assert result.envelope.action == "finalize"
    assert (
        result.envelope.message
        == "LLM provider is temporarily unavailable. Please try again shortly."
    )


def test_chat_cli_single_turn_progress(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat single-turn mode should stream progress and print assistant output."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["chat", "--message", "hello", "--session", "s-chat-cli"])

    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    lines = [line.strip() for line in clean_stdout.splitlines() if line.strip()]
    assert lines
    assert lines[0] == "AFK Agent"
    assert any(line.startswith("thinking") for line in lines[:-1])
    assert not any(line.startswith("planning") for line in lines[:-1])
    assert not any(line == "response ready" for line in lines[:-1])
    assert "AFK Agent" in clean_stdout
    assert "LLM provider is temporarily unavailable." in clean_stdout


def test_chat_cli_rejects_invalid_profile_id(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat CLI should fail with a plain usage error for unsafe profile ids."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["chat", "--profile", "Default", "--message", "hello"])

    assert result.exit_code == 2
    assert "Invalid profile id: Default" in result.stderr


def test_chat_cli_uses_profile_scoped_default_session_ids(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Different profiles should not collide on one implicit CLI session id."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    default_result = runner.invoke(app, ["chat", "--message", "hello default"])
    smoke_result = runner.invoke(app, ["chat", "--profile", "smoke", "--message", "hello smoke"])

    assert default_result.exit_code == 0
    assert smoke_result.exit_code == 0
    assert "SessionProfileMismatchError" not in smoke_result.stdout
    assert "LLM provider is temporarily unavailable." in smoke_result.stdout


def test_chat_cli_repl_smoke_via_stdin(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat REPL should process one turn from stdin and exit on slash command."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["chat", "--session", "s-chat-repl"], input="hello\n//quit\n")

    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    assert "you >" in clean_stdout
    assert "AFK Agent" in clean_stdout
    assert "LLM provider is temporarily unavailable." in clean_stdout


def test_chat_cli_repl_reuses_single_event_loop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """REPL should reuse one asyncio loop across multiple turns."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    seen_loop_ids: list[int] = []

    async def _fake_run_once_result(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls=None,
        progress_sink=None,
        context_overrides=None,
    ):
        _ = message, profile_id, session_id, planned_tool_calls, progress_sink, context_overrides
        seen_loop_ids.append(id(asyncio.get_running_loop()))
        return await run_once_result(message="hello", profile_id="default", session_id="s")

    monkeypatch.setattr("afkbot.cli.commands.chat.run_once_result", _fake_run_once_result)
    result = runner.invoke(
        app, ["chat", "--session", "s-chat-repl-loop"], input="first\nsecond\n//quit\n"
    )

    assert result.exit_code == 0
    assert len(seen_loop_ids) == 2
    assert seen_loop_ids[0] == seen_loop_ids[1]


def test_chat_cli_plan_on_runs_plan_then_execution(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Plan-first mode should run one plan-only turn before the execution turn."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    calls: list[dict[str, object]] = []
    confirmations = iter([True])

    async def _fake_run_once_result(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls=None,
        progress_sink=None,
        context_overrides=None,
    ):
        _ = planned_tool_calls, progress_sink
        calls.append(
            {
                "message": message,
                "profile_id": profile_id,
                "session_id": session_id,
                "context_overrides": context_overrides,
            }
        )
        if context_overrides is not None and context_overrides.planning_mode == "plan_only":
            return TurnResult(
                run_id=1,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="1. Inspect\n2. Implement"),
            )
        return TurnResult(
            run_id=2,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    monkeypatch.setattr("afkbot.cli.commands.chat.run_once_result", _fake_run_once_result)
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_planning_runtime.confirm_chat_plan_first",
        lambda **_: next(confirmations),
    )

    result = runner.invoke(
        app,
        [
            "chat",
            "--message",
            "Implement channel routing and update the docs after that.",
            "--session",
            "s-plan-cli",
            "--plan",
            "on",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 2
    assert calls[0]["context_overrides"] is not None
    assert calls[0]["context_overrides"].planning_mode == "plan_only"
    assert calls[0]["context_overrides"].execution_planning_mode == "off"
    assert calls[1]["context_overrides"] is not None
    assert calls[1]["context_overrides"].planning_mode == "off"
    assert calls[1]["context_overrides"].execution_planning_mode == "off"
    assert "AFK Plan" in strip_ansi(result.stdout)
    assert "[ ] Inspect" in strip_ansi(result.stdout)
    assert "[ ] Implement" in strip_ansi(result.stdout)


def test_build_plan_only_overrides_merges_expected_plan_only_context() -> None:
    """Plan-only overrides should always return one merged non-empty override object."""

    overrides = build_plan_only_overrides(
        base_overrides=TurnContextOverrides(
            runtime_metadata={"source": "cli"},
            prompt_overlay="Base instructions.",
        ),
        thinking_level="low",
    )

    assert overrides.runtime_metadata == {
        "source": "cli",
    }
    assert "Base instructions." in (overrides.prompt_overlay or "")
    assert "Return only the plan." in (overrides.prompt_overlay or "")
    assert overrides.planning_mode == "plan_only"
    assert overrides.execution_planning_mode == "off"
    assert overrides.thinking_level == "high"
    assert overrides.tool_access_mode == "read_only"


def test_build_cli_runtime_overrides_enables_cli_approval_surface() -> None:
    """afk chat should always enable the trusted CLI approval surface."""

    overrides = build_cli_runtime_overrides(
        target=RuntimeTarget(profile_id="default", session_id="cli:default"),
        transport=None,
        account_id=None,
        peer_id=None,
        thread_id=None,
        user_id=None,
    )

    assert overrides is not None
    assert overrides.cli_approval_surface_enabled is True
    assert overrides.runtime_metadata == {"transport": "cli"}
    assert "explicit user approval" in (overrides.prompt_overlay or "")


def test_chat_cli_single_turn_json_mode(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat single-turn JSON mode should keep machine-readable payload."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["chat", "--message", "hello", "--session", "s-chat-cli-json", "--json"],
    )

    assert result.exit_code == 0
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("{")
    assert '"action":"finalize"' in lines[0]


def test_chat_cli_single_turn_auto_plan_does_not_prompt_without_tty(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """One-shot non-TTY chat should not auto-open plan confirmation prompts."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    async def _fake_run_once_result(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls=None,
        progress_sink=None,
        context_overrides=None,
    ):
        _ = planned_tool_calls, progress_sink
        assert message == "Implement channel routing and update the docs after that."
        assert profile_id == "default"
        assert session_id == "s-auto-plan-no-tty"
        assert context_overrides is not None
        assert context_overrides.planning_mode == "off"
        assert context_overrides.execution_planning_mode == "auto"
        assert context_overrides.thinking_level == "medium"
        return TurnResult(
            run_id=3,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    monkeypatch.setattr("afkbot.cli.commands.chat.run_once_result", _fake_run_once_result)
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_planning_runtime.confirm_chat_plan_first",
        lambda **_: (_ for _ in ()).throw(AssertionError("confirm_space must not run without TTY")),
    )

    result = runner.invoke(
        app,
        [
            "chat",
            "--message",
            "Implement channel routing and update the docs after that.",
            "--session",
            "s-auto-plan-no-tty",
        ],
    )

    assert result.exit_code == 0
    assert "done" in result.stdout


def test_chat_help_describes_repl_and_json(monkeypatch: MonkeyPatch) -> None:
    """Chat help should explain one-shot vs interactive usage."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    runner = CliRunner()
    result, output = invoke_plain_help(runner, app, ["chat"])

    assert result.exit_code == 0
    assert "interactive" in output.lower()
    assert "--plan" in output
    assert "--json" in output


def test_chat_cli_uses_profile_runtime_thinking_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Chat CLI should resolve default thinking level from target profile runtime config."""

    _prepare_env(tmp_path, monkeypatch)
    settings = get_settings()
    ProfileRuntimeConfigService(settings).write(
        "planner",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_thinking_level="high",
            chat_planning_mode="off",
        ),
    )
    runner = CliRunner()

    async def _fake_run_once_result(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls=None,
        progress_sink=None,
        context_overrides=None,
    ):
        _ = planned_tool_calls, progress_sink
        assert message == "hello"
        assert profile_id == "planner"
        assert session_id == "s-profile-defaults"
        assert context_overrides is not None
        assert context_overrides.thinking_level == "high"
        assert context_overrides.planning_mode == "off"
        assert context_overrides.execution_planning_mode == "off"
        return TurnResult(
            run_id=4,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    monkeypatch.setattr("afkbot.cli.commands.chat.run_once_result", _fake_run_once_result)

    result = runner.invoke(
        app,
        [
            "chat",
            "--profile",
            "planner",
            "--message",
            "hello",
            "--session",
            "s-profile-defaults",
        ],
    )

    assert result.exit_code == 0
    assert "done" in result.stdout


def test_chat_repl_closes_browser_session_on_exit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive REPL should close sticky browser session for its chat session on exit."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    closed_calls: list[tuple[str, str]] = []

    class _FakeManager:
        async def close_session(  # type: ignore[no-untyped-def]
            self,
            *,
            root_dir,
            profile_id,
            session_id,
        ) -> bool:
            _ = root_dir
            closed_calls.append((profile_id, session_id))
            return True

    monkeypatch.setattr(
        "afkbot.cli.commands.chat.get_browser_session_manager",
        lambda: _FakeManager(),
    )

    result = runner.invoke(app, ["chat", "--session", "s-chat-exit"], input="//quit\n")

    assert result.exit_code == 0
    assert closed_calls == [("default", "s-chat-exit")]


def test_chat_repl_local_commands_update_future_turn_settings(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive chat should apply local `//plan` and `//thinking` commands to later turns."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    seen_overrides: list[TurnContextOverrides | None] = []

    async def _fake_run_once_result(  # type: ignore[no-untyped-def]
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls=None,
        progress_sink=None,
        context_overrides=None,
    ):
        _ = planned_tool_calls, progress_sink
        assert message == "hello"
        assert profile_id == "default"
        assert session_id == "s-chat-repl-controls"
        seen_overrides.append(context_overrides)
        return TurnResult(
            run_id=9,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="done"),
        )

    monkeypatch.setattr("afkbot.cli.commands.chat.run_once_result", _fake_run_once_result)

    # Act
    result = runner.invoke(
        app,
        ["chat", "--session", "s-chat-repl-controls"],
        input="//plan off\n//thinking high\nhello\n//quit\n",
    )

    # Assert
    assert result.exit_code == 0
    assert seen_overrides
    assert seen_overrides[0] is not None
    assert seen_overrides[0].execution_planning_mode == "off"
    assert seen_overrides[0].thinking_level == "high"
    assert "Planning mode updated to: off" in result.stdout
    assert "Thinking level updated to: high" in result.stdout


async def test_repl_turn_interruptibly_returns_none_after_first_cancel() -> None:
    """First cancellation should stop only the active turn and let REPL continue."""

    # Arrange
    cancel_notices: list[str] = []
    turn_cancelled = asyncio.Event()
    started = asyncio.Event()

    async def _fake_run_turn() -> TurnResult:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            turn_cancelled.set()
            await asyncio.sleep(0)
            raise

    task = asyncio.create_task(
        run_turn_interruptibly(
            task_name="chat_repl_turn:default:s-repl-cancel",
            run_turn=_fake_run_turn,
            on_interrupt=lambda: cancel_notices.append("interrupt"),
        )
    )
    await started.wait()

    # Act
    task.cancel()
    result = await task

    # Assert
    assert result is None
    assert cancel_notices == ["interrupt"]
    assert turn_cancelled.is_set()


async def test_repl_turn_interruptibly_reraises_second_cancel() -> None:
    """Second cancellation should escape so REPL can exit immediately."""

    # Arrange
    cancel_notices: list[str] = []
    first_cancel_seen = asyncio.Event()
    release_cleanup = asyncio.Event()
    started = asyncio.Event()
    inner_task: asyncio.Task[TurnResult] | None = None

    async def _fake_run_turn() -> TurnResult:
        nonlocal inner_task
        inner_task = asyncio.current_task()
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancel_seen.set()
            await release_cleanup.wait()
            raise

    task = asyncio.create_task(
        run_turn_interruptibly(
            task_name="chat_repl_turn:default:s-repl-double-cancel",
            run_turn=_fake_run_turn,
            on_interrupt=lambda: cancel_notices.append("interrupt"),
        )
    )
    await started.wait()

    # Act
    task.cancel()
    await first_cancel_seen.wait()
    task.cancel()

    # Assert
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancel_notices == ["interrupt"]
    release_cleanup.set()
    assert inner_task is not None
    with pytest.raises(asyncio.CancelledError):
        await inner_task
