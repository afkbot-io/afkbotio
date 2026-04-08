"""Tests for plugin CLI flows."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.cli.presentation.plugin_prompts import _is_supported_custom_plugin_source
from afkbot.settings import get_settings


def test_plugin_install_uses_prompted_source_when_argument_is_omitted(tmp_path, monkeypatch) -> None:
    """Plugin install should fall back to the interactive wizard source when no argument is provided."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    class _FakeInstalledRecord:
        def model_dump(self, mode: str = "json") -> dict[str, object]:
            assert mode == "json"
            return {"plugin_id": "afkbotui", "source_ref": captured["source"]}

    class _FakePluginService:
        def list_installed(self) -> tuple[object, ...]:
            return ()

        def install(self, *, source: str, enable: bool, overwrite: bool) -> _FakeInstalledRecord:
            captured["source"] = source
            captured["enable"] = enable
            captured["overwrite"] = overwrite
            return _FakeInstalledRecord()

    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.get_plugin_service",
        lambda _settings: _FakePluginService(),
    )
    monkeypatch.setattr("afkbot.cli.commands.plugin.supports_interactive_tty", lambda: True)
    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.prompt_plugin_install_source",
        lambda **_kwargs: "github:afkbot-io/afkbotuiplugin@main",
    )
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "install"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugin"]["plugin_id"] == "afkbotui"
    assert captured == {
        "source": "github:afkbot-io/afkbotuiplugin@main",
        "enable": True,
        "overwrite": False,
    }


def test_plugin_install_requires_source_when_wizard_returns_empty(tmp_path, monkeypatch) -> None:
    """Plugin install should fail cleanly when neither argument nor wizard provides a source."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FakePluginService:
        def list_installed(self) -> tuple[object, ...]:
            return ()

    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.get_plugin_service",
        lambda _settings: _FakePluginService(),
    )
    monkeypatch.setattr("afkbot.cli.commands.plugin.supports_interactive_tty", lambda: False)
    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.prompt_plugin_install_source",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("wizard should not run without TTY")),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "install"])

    assert result.exit_code == 2
    assert "Plugin source is required in non-interactive mode." in result.output


def test_plugin_install_custom_source_validation_accepts_github_sources() -> None:
    """Wizard custom source should accept GitHub shorthand and URLs."""

    assert _is_supported_custom_plugin_source("github:afkbot-io/afkbotuiplugin@main") is True
    assert _is_supported_custom_plugin_source("https://github.com/afkbot-io/afkbotuiplugin") is True


def test_plugin_install_custom_source_validation_rejects_non_github_sources() -> None:
    """Wizard custom source should reject local paths and non-GitHub URLs."""

    assert _is_supported_custom_plugin_source("") is False
    assert _is_supported_custom_plugin_source("./local-plugin") is False
    assert _is_supported_custom_plugin_source("https://example.com/plugin.tar.gz") is False
