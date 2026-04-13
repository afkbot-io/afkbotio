"""Tests for plugin CLI flows."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.cli.presentation.plugin_prompts import (
    _is_supported_custom_plugin_source,
    _plugin_install_prompt_text,
)
from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.plugins.contracts import InstalledPluginRecord, PluginConfigMetadata
from afkbot.settings import get_settings


def _plugin_record(
    *,
    plugin_id: str = "afkbotui",
    name: str = "AFKBOT UI",
    enabled: bool = True,
) -> InstalledPluginRecord:
    return InstalledPluginRecord.model_validate(
        {
            "plugin_id": plugin_id,
            "name": name,
            "version": "0.2.0",
            "enabled": enabled,
            "source_kind": "github_archive",
            "source_ref": f"github:afkbot-io/{plugin_id}plugin@main",
            "install_path": f"plugins/packages/{plugin_id}/0.2.0",
            "installed_at": "2026-04-08T21:29:05.877075Z",
            "manifest": {
                "plugin_id": plugin_id,
                "name": name,
                "version": "0.2.0",
                "afkbot_version": ">=1.0.7,<2.0.0",
                "entrypoint": f"afkbot_plugin_{plugin_id}.plugin:register",
                "description": f"{name} plugin.",
                "default_config": {
                    "poll_interval_sec": 5,
                    "default_profile_id": "default",
                },
                "config_schema": {
                    "fields": {
                        "poll_interval_sec": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "default_profile_id": {
                            "type": "string",
                            "min_length": 1,
                        },
                    }
                },
                "permissions": {
                    "database": "read_write",
                    "taskflow": "read_write",
                    "outbound_http": False,
                    "data_dir_write": False,
                },
                "capabilities": {
                    "api_router": True,
                    "static_web": True,
                },
                "mounts": {
                    "api_prefix": f"/v1/plugins/{plugin_id}",
                    "web_prefix": f"/plugins/{plugin_id}",
                },
                "paths": {
                    "python_root": "python",
                    "web_root": "web/dist",
                },
            },
        }
    )


def _plugin_config(*, plugin_id: str = "afkbotui") -> PluginConfigMetadata:
    return PluginConfigMetadata.model_validate(
        {
            "plugin_id": plugin_id,
            "source": "persisted",
            "config_path": f"/tmp/{plugin_id}.json",
            "data_dir": f"/tmp/{plugin_id}",
            "config_schema": {
                "fields": {
                    "poll_interval_sec": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "default_profile_id": {
                        "type": "string",
                        "min_length": 1,
                    },
                }
            },
            "config": {
                "poll_interval_sec": 5,
                "default_profile_id": "default",
            },
        }
    )


def test_plugin_install_uses_prompted_source_when_argument_is_omitted(tmp_path, monkeypatch) -> None:
    """Plugin install should fall back to the interactive wizard source when no argument is provided."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    captured: dict[str, object] = {}
    installed = _plugin_record(plugin_id="kanban", name="Task Flow Kanban")

    class _FakePluginService:
        def list_installed(self) -> tuple[InstalledPluginRecord, ...]:
            return (installed,)

        def install(self, *, source: str, enable: bool, overwrite: bool) -> InstalledPluginRecord:
            captured["source"] = source
            captured["enable"] = enable
            captured["overwrite"] = overwrite
            return _plugin_record()

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
    assert "Plugin installed: afkbotui" in result.stdout
    assert "source_ref: github:afkbot-io/afkbotuiplugin@main" in result.stdout
    assert captured == {
        "source": "github:afkbot-io/afkbotuiplugin@main",
        "enable": True,
        "overwrite": False,
    }


def test_plugin_install_json_requires_source_when_argument_is_omitted(tmp_path, monkeypatch) -> None:
    """Plugin install JSON mode should stay deterministic and require an explicit source."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "install", "--json"])

    assert result.exit_code == 2
    assert "Plugin source is required when --json is used." in result.output


def test_plugin_install_requires_source_when_wizard_returns_empty(tmp_path, monkeypatch) -> None:
    """Plugin install should fail cleanly when neither argument nor wizard provides a source."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FakePluginService:
        def list_installed(self) -> tuple[InstalledPluginRecord, ...]:
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


def test_plugin_list_renders_human_readable_output(tmp_path, monkeypatch) -> None:
    """Plugin list should default to readable text instead of raw JSON."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FakePluginService:
        def list_installed(self) -> tuple[InstalledPluginRecord, ...]:
            return (_plugin_record(),)

    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.get_plugin_service",
        lambda _settings: _FakePluginService(),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "list"])

    assert result.exit_code == 0
    assert "Installed plugins: 1" in result.stdout
    assert "- afkbotui: AFKBOT UI v0.2.0, enabled=True" in result.stdout


def test_plugin_list_json_output_still_available(tmp_path, monkeypatch) -> None:
    """Plugin list should still expose deterministic JSON when requested."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FakePluginService:
        def list_installed(self) -> tuple[InstalledPluginRecord, ...]:
            return (_plugin_record(),)

    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.get_plugin_service",
        lambda _settings: _FakePluginService(),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["plugins"][0]["plugin_id"] == "afkbotui"


def test_plugin_config_get_renders_human_readable_output(tmp_path, monkeypatch) -> None:
    """Plugin config-get should render readable config details by default."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()

    class _FakePluginService:
        def get_config(self, *, plugin_id: str) -> PluginConfigMetadata:
            assert plugin_id == "afkbotui"
            return _plugin_config(plugin_id=plugin_id)

    monkeypatch.setattr(
        "afkbot.cli.commands.plugin.get_plugin_service",
        lambda _settings: _FakePluginService(),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "config-get", "afkbotui"])

    assert result.exit_code == 0
    assert "Plugin config: afkbotui" in result.stdout
    assert "poll_interval_sec: 5" in result.stdout
    assert "default_profile_id: default" in result.stdout


def test_plugin_install_prompt_text_includes_installed_summary() -> None:
    """Install wizard text should show how many plugins are already installed."""

    text = _plugin_install_prompt_text(
        lang=PromptLanguage.EN,
        installed_plugin_labels=("afkbotui", "kanban"),
    )

    assert "Installed plugins: 2 (afkbotui, kanban)." in text


def test_plugin_install_custom_source_validation_accepts_github_sources() -> None:
    """Wizard custom source should accept GitHub shorthand and URLs."""

    assert _is_supported_custom_plugin_source("github:afkbot-io/afkbotuiplugin@main") is True
    assert _is_supported_custom_plugin_source("https://github.com/afkbot-io/afkbotuiplugin") is True


def test_plugin_install_custom_source_validation_rejects_non_github_sources() -> None:
    """Wizard custom source should reject local paths and non-GitHub URLs."""

    assert _is_supported_custom_plugin_source("") is False
    assert _is_supported_custom_plugin_source("./local-plugin") is False
    assert _is_supported_custom_plugin_source("https://example.com/plugin.tar.gz") is False
