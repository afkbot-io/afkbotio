"""CLI-facing setup command tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
import importlib
import json
from pathlib import Path
import sys
import types

from pytest import MonkeyPatch
from typer.testing import CliRunner

try:
    importlib.import_module("mcp.types")
except ImportError:
    mcp_module = types.ModuleType("mcp")
    mcp_types_module = types.ModuleType("mcp.types")
    mcp_client_module = types.ModuleType("mcp.client")
    mcp_client_sse_module = types.ModuleType("mcp.client.sse")
    mcp_client_streamable_http_module = types.ModuleType("mcp.client.streamable_http")
    mcp_client_websocket_module = types.ModuleType("mcp.client.websocket")

    class _Tool:
        def __init__(
            self,
            name: str = "",
            inputSchema: dict[str, object] | None = None,
            description: str | None = None,
            title: str | None = None,
        ) -> None:
            self.name = name
            self.inputSchema = {} if inputSchema is None else inputSchema
            self.description = description
            self.title = title

    class _ListToolsResult:
        def __init__(self, tools: tuple[object, ...] = ()) -> None:
            self.tools = tools

    class _CallToolResult:
        pass

    class _ClientSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_ClientSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> _ListToolsResult:
            return _ListToolsResult()

        async def call_tool(self, *_args: object, **_kwargs: object) -> _CallToolResult:
            return _CallToolResult()

    @asynccontextmanager
    async def _sse_client(*_args: object, **_kwargs: object):
        yield (None, None)

    @asynccontextmanager
    async def _streamablehttp_client(*_args: object, **_kwargs: object):
        yield (None, None, None)

    @asynccontextmanager
    async def _websocket_client(*_args: object, **_kwargs: object):
        yield (None, None)

    mcp_module.ClientSession = _ClientSession
    mcp_module.types = mcp_types_module
    mcp_types_module.Tool = _Tool
    mcp_types_module.ListToolsResult = _ListToolsResult
    mcp_types_module.CallToolResult = _CallToolResult
    mcp_client_sse_module.sse_client = _sse_client
    mcp_client_streamable_http_module.streamablehttp_client = _streamablehttp_client
    mcp_client_websocket_module.websocket_client = _websocket_client
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.types"] = mcp_types_module
    sys.modules["mcp.client"] = mcp_client_module
    sys.modules["mcp.client.sse"] = mcp_client_sse_module
    sys.modules["mcp.client.streamable_http"] = mcp_client_streamable_http_module
    sys.modules["mcp.client.websocket"] = mcp_client_websocket_module

from afkbot.cli.main import app
from afkbot.cli.commands.setup_support import load_current_default_profile
from afkbot.services.profile_runtime import ProfileServiceError
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.settings import get_settings
from tests.cli._setup_harness import bootstrap_platform, prepare_root


def test_setup_cli_runs_without_prior_platform_bootstrap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Public setup should work directly in a local source checkout."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 0
    assert (tmp_path / "profiles/.system/setup_state.json").exists()


def test_setup_cli_seeds_global_bootstrap_files(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Setup should create the global bootstrap files required by doctor and runtime flows."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 0
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        assert (tmp_path / "afkbot/bootstrap" / file_name).exists()


def test_setup_cli_bootstrap_only_seeds_platform_runtime_without_marker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Bootstrap-only flow should seed runtime config and stop before profile setup."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)

    # Act
    payload = bootstrap_platform(tmp_path)

    # Assert
    assert payload["ok"] is True
    assert payload["database"] == "sqlite"
    runtime_config_path = tmp_path / "profiles/.system/runtime_config.json"
    setup_state_path = tmp_path / "profiles/.system/setup_state.json"
    assert runtime_config_path.exists()
    assert not setup_state_path.exists()
    assert not (tmp_path / "profiles/default/.system").exists()


def test_setup_cli_bootstrap_only_creates_missing_root_directory(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Bootstrap-only install should create a missing root directory safely."""

    # Arrange
    missing_root = tmp_path / "missing-root"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(missing_root))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{missing_root / 'afkbot.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AFKBOT_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("AFKBOT_OPENAI_API_KEY", "seed-key")
    get_settings.cache_clear()
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "setup",
            "--bootstrap-only",
            "--yes",
            "--accept-risk",
            "--skip-llm-token-verify",
        ],
    )

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["database"] == "sqlite"
    assert (missing_root / "profiles/.system/runtime_config.json").exists()
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        assert (missing_root / "afkbot/bootstrap" / file_name).exists()
    assert not (missing_root / "profiles/.system/setup_state.json").exists()
    assert not (missing_root / "profiles/default").exists()


def test_setup_cli_bootstrap_only_persists_installer_source_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Bootstrap-only setup should persist installer source metadata for later `afk update` runs."""

    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setenv("AFKBOT_INSTALL_SOURCE_MODE", "archive")
    monkeypatch.setenv(
        "AFKBOT_INSTALL_SOURCE_SPEC",
        "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.command_runtime.resolve_install_source_target",
        lambda install_source: None,
    )

    payload = bootstrap_platform(tmp_path)

    assert payload["ok"] is True
    config = read_runtime_config(get_settings())
    assert config["install_source_mode"] == "archive"
    assert config["install_source_spec"] == "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz"


def test_setup_cli_bootstrap_only_persists_resolved_installer_target(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Bootstrap-only setup should persist a resolved installer target for update notices."""

    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setenv("AFKBOT_INSTALL_SOURCE_MODE", "archive")
    monkeypatch.setenv(
        "AFKBOT_INSTALL_SOURCE_SPEC",
        "https://github.com/afkbot-io/afkbotio/archive/main.tar.gz",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.command_runtime.resolve_install_source_target",
        lambda install_source: "abcdef1234567890",
    )

    payload = bootstrap_platform(tmp_path)

    assert payload["ok"] is True
    config = read_runtime_config(get_settings())
    assert config["install_source_resolved_target"] == "abcdef1234567890"


def test_setup_cli_detects_russian_language_from_system_locale(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Setup should persist Russian prompt language when the local system locale is Russian."""

    prepare_root(tmp_path, monkeypatch)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    assert result.exit_code == 0
    config = read_runtime_config(get_settings())
    assert config["prompt_language"] == "ru"


def test_setup_cli_lang_flag_overrides_detected_system_locale(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Explicit `--lang` should win over detected system locale."""

    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify", "--lang", "ru"],
    )

    assert result.exit_code == 0
    config = read_runtime_config(get_settings())
    assert config["prompt_language"] == "ru"


def test_setup_cli_can_disable_chat_update_notices(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Setup should persist the operator preference for chat-time update prompts."""

    prepare_root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "--yes",
            "--accept-risk",
            "--skip-llm-token-verify",
            "--no-update-notices",
        ],
    )

    assert result.exit_code == 0
    config = read_runtime_config(get_settings())
    assert config["update_notices_enabled"] is False


def test_setup_cli_yes_requires_accept_risk(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Non-interactive setup must require explicit security acknowledgment."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 2
    assert "Use --accept-risk in --yes mode." in result.output


def test_setup_cli_yes_requires_accept_risk_in_russian(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Non-interactive setup should localize the accept-risk error when Russian is requested."""

    prepare_root(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["setup", "--yes", "--skip-llm-token-verify", "--lang", "ru"])

    assert result.exit_code == 2
    assert "Используйте --accept-risk вместе с --yes." in result.output


def test_setup_cli_requests_managed_runtime_reload_after_public_setup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Public setup should ask the local managed runtime to reload after config changes."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda settings: calls.append(str(settings.root_dir)),
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 0
    assert calls == [str(tmp_path)]


def test_setup_cli_formats_profile_service_errors_via_runtime_formatter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile service failures should use the dedicated setup runtime formatter path."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    runner = CliRunner()

    def _raise_profile_service_error(**_kwargs: object) -> object:
        raise ProfileServiceError(
            error_code="profile_invalid_name",
            reason="profile reason",
        )

    monkeypatch.setattr(
        "afkbot.cli.commands.setup.execute_setup_runtime",
        _raise_profile_service_error,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.format_setup_runtime_error",
        lambda exc: f"formatted::{exc.reason}",
    )

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 2
    assert "formatted::profile reason" in result.output


def test_load_current_default_profile_returns_none_when_runtime_db_is_unavailable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Setup helper should ignore unavailable runtime DB while probing current defaults."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    settings = get_settings()

    def _raise_connection_refused(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 55432)")

    monkeypatch.setattr(
        "afkbot.cli.commands.setup_support.run_profile_service_sync",
        _raise_connection_refused,
    )

    # Act
    result = load_current_default_profile(settings)

    # Assert
    assert result is None


def test_setup_cli_ignores_unavailable_runtime_db_while_loading_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Setup CLI should continue when a stale configured runtime DB is unreachable."""

    # Arrange
    prepare_root(tmp_path, monkeypatch)
    runner = CliRunner()

    def _raise_connection_refused(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 55432)")

    monkeypatch.setattr(
        "afkbot.cli.commands.setup_support.run_profile_service_sync",
        _raise_connection_refused,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.execute_setup_runtime",
        lambda **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.setup.reload_install_managed_runtime_notice",
        lambda _settings: None,
    )

    # Act
    result = runner.invoke(app, ["setup", "--yes", "--accept-risk", "--skip-llm-token-verify"])

    # Assert
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == {"ok": True}
