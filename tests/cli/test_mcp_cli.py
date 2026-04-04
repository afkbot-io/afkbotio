"""Tests for MCP CLI command surface."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.cli.presentation.mcp_wizard import prompt_optional_refs, prompt_resolved_mcp_url
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'mcp-cli.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def _create_profile(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "default",
            "--name",
            "Default",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert result.exit_code == 0


def test_mcp_cli_add_list_and_validate_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The MCP CLI should add one remote server and expose it via list/validate commands."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    # Act
    add_result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--profile",
            "default",
            "--url",
            "https://example.com/mcp",
            "--secret-ref",
            "mcp_example_token",
            "--yes",
            "--json",
        ],
    )
    list_result = runner.invoke(app, ["mcp", "list", "--profile", "default", "--json"])
    validate_result = runner.invoke(app, ["mcp", "validate", "--profile", "default", "--json"])

    # Assert
    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["result"]["server"]["server"] == "example"
    assert add_payload["result"]["target_path"] == "profiles/default/mcp.json"
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["servers"][0]["server"] == "example"
    assert list_payload["servers"][0]["url"] == "https://example.com/mcp"
    assert list_payload["servers"][0]["access"]["runtime_available"] is True
    assert validate_result.exit_code == 0
    validate_payload = json.loads(validate_result.stdout)
    assert validate_payload["report"]["ok"] is True
    assert validate_payload["report"]["notes"] == [
        "Profile MCP configuration uses `afk mcp` or `mcp.profile.*`. Runtime MCP tool access uses `mcp.tools.list` / `mcp.tools.call` for enabled remote servers with `tools` capability and matching policy/network access."
    ]


def test_mcp_cli_add_reports_invalid_url_without_traceback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk mcp add --url ...` should fail cleanly for malformed URLs."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    # Act
    result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--profile",
            "default",
            "--url",
            "s",
            "--yes",
        ],
    )

    # Assert
    assert result.exit_code == 1
    assert "MCP URL scheme must be one of: http, https, ws, wss" in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_mcp_cli_connect_and_get_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`afk mcp connect <url>` and `afk mcp get <server>` should work for manual URL-driven flows."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    connect_result = runner.invoke(
        app,
        [
            "mcp",
            "connect",
            "https://example.com/mcp",
            "--profile",
            "default",
            "--secret-ref",
            "mcp_example_token",
            "--yes",
            "--json",
        ],
    )
    get_result = runner.invoke(
        app,
        ["mcp", "get", "example", "--profile", "default", "--json"],
    )

    assert connect_result.exit_code == 0
    connect_payload = json.loads(connect_result.stdout)
    assert connect_payload["result"]["server"]["server"] == "example"
    assert get_result.exit_code == 0
    get_payload = json.loads(get_result.stdout)
    assert get_payload["server"]["url"] == "https://example.com/mcp"
    assert get_payload["server"]["secret_refs"] == ["mcp_example_token"]


def test_mcp_cli_edit_and_remove_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The MCP CLI should update one existing remote server and then remove it cleanly."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)
    add_result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--profile",
            "default",
            "--url",
            "https://example.com/mcp",
            "--secret-ref",
            "mcp_example_token",
            "--yes",
            "--json",
        ],
    )
    assert add_result.exit_code == 0

    # Act
    edit_result = runner.invoke(
        app,
        [
            "mcp",
            "edit",
            "--profile",
            "default",
            "--server",
            "example",
            "--url",
            "wss://example.com/ws",
            "--transport",
            "websocket",
            "--capability",
            "tools",
            "--capability",
            "resources",
            "--secret-ref",
            "mcp_example_next_token",
            "--disabled",
            "--yes",
            "--json",
        ],
    )
    list_result = runner.invoke(
        app,
        ["mcp", "list", "--profile", "default", "--show-disabled", "--json"],
    )
    remove_result = runner.invoke(
        app,
        [
            "mcp",
            "remove",
            "--profile",
            "default",
            "--server",
            "example",
            "--yes",
            "--json",
        ],
    )
    final_list_result = runner.invoke(
        app,
        ["mcp", "list", "--profile", "default", "--show-disabled", "--json"],
    )

    # Assert
    assert edit_result.exit_code == 0
    edit_payload = json.loads(edit_result.stdout)
    assert edit_payload["result"]["server"]["url"] == "wss://example.com/ws"
    assert edit_payload["result"]["server"]["transport"] == "websocket"
    assert edit_payload["result"]["server"]["enabled"] is False
    list_payload = json.loads(list_result.stdout)
    assert list_payload["servers"][0]["server"] == "example"
    assert list_payload["servers"][0]["enabled"] is False
    assert list_payload["servers"][0]["secret_refs"] == ["mcp_example_next_token"]
    assert remove_result.exit_code == 0
    remove_payload = json.loads(remove_result.stdout)
    assert remove_payload["result"]["removed_server"] == "example"
    final_list_payload = json.loads(final_list_result.stdout)
    assert final_list_payload["servers"] == []


def test_mcp_cli_treats_server_ids_case_insensitively(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI add/list/remove should normalize mixed-case server ids consistently."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    # Act
    add_result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--profile",
            "default",
            "--url",
            "https://example.com/mcp",
            "--server",
            "MyServer",
            "--secret-ref",
            "mcp_example_token",
            "--yes",
            "--json",
        ],
    )
    list_result = runner.invoke(
        app,
        ["mcp", "list", "--profile", "default", "--json"],
    )
    remove_result = runner.invoke(
        app,
        ["mcp", "remove", "--profile", "default", "--server", "MYSERVER", "--yes", "--json"],
    )

    # Assert
    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["result"]["server"]["server"] == "myserver"
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["servers"][0]["server"] == "myserver"
    assert remove_result.exit_code == 0
    remove_payload = json.loads(remove_result.stdout)
    assert remove_payload["result"]["removed_server"] == "myserver"


def test_mcp_cli_edit_reports_invalid_stored_url_without_traceback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk mcp edit` should fail cleanly when the stored MCP URL is malformed."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    class _FakeService:
        async def get(self, *, profile_id: str, server: str) -> SimpleNamespace:
            _ = profile_id, server
            return SimpleNamespace(
                server="example",
                url="s",
                transport="http",
                capabilities=("tools",),
                env_refs=(),
                secret_refs=(),
                enabled=True,
            )

        async def preview_add_by_url(self, **kwargs: object) -> object:
            raise AssertionError("preview_add_by_url must not run for invalid stored URLs")

        async def add_by_url(self, **kwargs: object) -> object:
            raise AssertionError("add_by_url must not run for invalid stored URLs")

    monkeypatch.setattr("afkbot.cli.commands.mcp.get_settings", lambda: object())
    monkeypatch.setattr(
        "afkbot.cli.commands.mcp.get_mcp_profile_service",
        lambda _settings: _FakeService(),
    )
    monkeypatch.setattr("afkbot.cli.commands.mcp.mcp_wizard_enabled", lambda: False)

    # Act
    result = runner.invoke(
        app,
        [
            "mcp",
            "edit",
            "--profile",
            "default",
            "--server",
            "example",
        ],
    )

    # Assert
    assert result.exit_code == 1
    assert "MCP URL scheme must be one of: http, https, ws, wss" in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr


def test_mcp_cli_edit_uses_url_resolution_suggestions_for_optional_refs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive edit should reuse URL-derived ref suggestions instead of hardcoded hints."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    captured_suggestions: list[tuple[str, str]] = []

    class _FakeService:
        async def get(self, *, profile_id: str, server: str) -> SimpleNamespace:
            _ = profile_id, server
            return SimpleNamespace(
                server="research",
                url="https://example.com/research/sse",
                transport="sse",
                capabilities=("tools",),
                env_refs=("MCP_RESEARCH_BASE_URL",),
                secret_refs=("mcp_research_token",),
                enabled=True,
            )

        async def preview_add_by_url(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                server=SimpleNamespace(
                    server="research",
                    transport="sse",
                    capabilities=("tools",),
                    env_refs=("MCP_RESEARCH_BASE_URL",),
                    secret_refs=("mcp_research_token",),
                    enabled=True,
                ),
                target_path="profiles/default/mcp.json",
                storage_mode="shared",
            )

        async def add_by_url(self, **kwargs: object) -> SimpleNamespace:
            raise AssertionError("add_by_url should not run when confirmation is declined")

    monkeypatch.setattr("afkbot.cli.commands.mcp.get_settings", lambda: object())
    monkeypatch.setattr(
        "afkbot.cli.commands.mcp.get_mcp_profile_service",
        lambda _settings: _FakeService(),
    )
    monkeypatch.setattr("afkbot.cli.commands.mcp.mcp_wizard_enabled", lambda: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.mcp.prompt_mcp_transport",
        lambda *, default: str(default),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.mcp.prompt_mcp_capabilities",
        lambda *, defaults: tuple(defaults),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.mcp.prompt_optional_refs",
        lambda *, label, suggestion, default_values: captured_suggestions.append((label, suggestion))
        or tuple(default_values),
    )
    monkeypatch.setattr("afkbot.cli.commands.mcp.confirm_mcp_add", lambda *, preview_text: False)

    # Act
    result = runner.invoke(
        app,
        [
            "mcp",
            "edit",
            "--profile",
            "default",
            "--server",
            "research",
        ],
    )

    # Assert
    assert result.exit_code == 0
    assert captured_suggestions == [
        ("Environment refs", "MCP_RESEARCH_BASE_URL"),
        ("Secret refs", "mcp_research_token"),
    ]


def test_prompt_resolved_mcp_url_retries_until_valid(monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The interactive MCP URL prompt should re-prompt after one invalid attempt."""

    # Arrange
    answers = iter(["s", "https://example.com/mcp"])
    monkeypatch.setattr(
        "afkbot.cli.presentation.mcp_wizard.typer.prompt",
        lambda *args, **kwargs: next(answers),
    )

    # Act
    resolution = prompt_resolved_mcp_url()

    # Assert
    captured = capsys.readouterr()
    assert "Invalid MCP URL: MCP URL scheme must be one of: http, https, ws, wss" in captured.out
    assert resolution.url == "https://example.com/mcp"


def test_prompt_optional_refs_keeps_existing_defaults_on_empty_input(monkeypatch: MonkeyPatch) -> None:
    """Interactive MCP ref prompts should preserve the current values on empty submit."""

    # Arrange
    monkeypatch.setattr(
        "afkbot.cli.presentation.mcp_wizard.typer.prompt",
        lambda *args, **kwargs: kwargs.get("default", ""),
    )

    # Act
    refs = prompt_optional_refs(
        label="Secret refs",
        suggestion="mcp_example_token",
        default_values=("mcp_existing_token",),
    )

    # Assert
    assert refs == ("mcp_existing_token",)



def test_mcp_cli_add_requires_url_in_json_mode_without_tty(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk mcp add --json` should return a machine-readable usage error when URL is missing."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["mcp", "add", "--json"])

    # Assert
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "usage_error"
    assert payload["reason"] == "--url is required without an interactive TTY"


def test_mcp_cli_add_json_mode_never_opens_interactive_wizard(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk mcp add --json` should fail fast instead of prompting even on an interactive TTY."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    prompt_calls: list[str] = []
    monkeypatch.setattr("afkbot.cli.commands.mcp.mcp_wizard_enabled", lambda: True)
    monkeypatch.setattr(
        "afkbot.cli.presentation.mcp_wizard.typer.prompt",
        lambda label, **kwargs: prompt_calls.append(str(label)) or "https://example.com/mcp",
    )

    # Act
    result = runner.invoke(app, ["mcp", "add", "--json"])

    # Assert
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "usage_error"
    assert payload["reason"] == "--url is required without an interactive TTY"
    assert prompt_calls == []



def test_mcp_cli_remove_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The MCP CLI should remove one previously added operator-managed server."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)
    add_result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--profile",
            "default",
            "--url",
            "https://example.com/mcp",
            "--secret-ref",
            "mcp_example_token",
            "--yes",
            "--json",
        ],
    )
    assert add_result.exit_code == 0

    # Act
    remove_result = runner.invoke(
        app,
        [
            "mcp",
            "remove",
            "--profile",
            "default",
            "--server",
            "example",
            "--yes",
            "--json",
        ],
    )
    list_result = runner.invoke(app, ["mcp", "list", "--profile", "default", "--json"])

    # Assert
    assert remove_result.exit_code == 0
    remove_payload = json.loads(remove_result.stdout)
    assert remove_payload["result"]["removed_server"] == "example"
    assert list_result.exit_code == 0
    assert json.loads(list_result.stdout)["servers"] == []


def test_mcp_cli_remove_reports_missing_server_without_traceback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk mcp remove --server ...` should fail cleanly when nothing managed matches."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    # Act
    result = runner.invoke(
        app,
        [
            "mcp",
            "remove",
            "--profile",
            "default",
            "--server",
            "missing",
            "--yes",
        ],
    )

    # Assert
    assert result.exit_code == 1
    assert "MCP server not found: missing" in result.stdout
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
