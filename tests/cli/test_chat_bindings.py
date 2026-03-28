"""Tests for chat CLI binding-aware target resolution."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


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
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def _add_profile(runner: CliRunner, profile_id: str, name: str) -> None:
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            profile_id,
            "--name",
            name,
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert result.exit_code == 0


def test_chat_cli_resolves_binding_target_before_turn(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Chat CLI should resolve effective profile/session from persisted bindings."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _add_profile(runner, "sales", "Sales")

    binding_result = runner.invoke(
        app,
        [
            "profile",
            "binding",
            "set",
            "telegram-sales",
            "--transport",
            "telegram",
            "--profile-id",
            "sales",
            "--session-policy",
            "per-thread",
            "--peer-id",
            "42",
        ],
    )
    assert binding_result.exit_code == 0

    captured: dict[str, object] = {}

    def _fake_run_single_turn(**kwargs) -> None:  # type: ignore[no-untyped-def]
        captured.update(kwargs)

    monkeypatch.setattr("afkbot.cli.commands.chat.run_single_turn", _fake_run_single_turn)

    result = runner.invoke(
        app,
        [
            "chat",
            "--message",
            "hello",
            "--resolve-binding",
            "--transport",
            "telegram",
            "--peer-id",
            "42",
            "--thread-id",
            "9001",
        ],
    )

    assert result.exit_code == 0
    assert captured["message"] == "hello"
    assert captured["profile_id"] == "sales"
    assert captured["session_id"] == "profile:sales:chat:42:thread:9001"


def test_chat_cli_requires_transport_when_binding_resolution_enabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Binding-aware chat mode should require transport metadata."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["chat", "--message", "hello", "--resolve-binding"])

    assert result.exit_code != 0
    assert "--transport is required with --resolve-binding" in result.stderr


def test_chat_cli_can_require_binding_match(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Strict binding mode should fail when no persisted rule matches the selectors."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "chat",
            "--message",
            "hello",
            "--resolve-binding",
            "--require-binding-match",
            "--transport",
            "telegram",
            "--peer-id",
            "42",
        ],
    )

    assert result.exit_code != 0
    assert "No channel binding matched the provided target selectors." in result.stderr


def test_chat_cli_external_binding_resolution_is_strict_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """External transport binding resolution should fail closed without an explicit match."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "chat",
            "--message",
            "hello",
            "--resolve-binding",
            "--transport",
            "telegram",
            "--peer-id",
            "42",
        ],
    )

    assert result.exit_code != 0
    assert "No channel binding matched the provided target selectors." in result.stderr
