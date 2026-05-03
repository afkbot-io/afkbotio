"""Tests for localized setup completion messaging."""

from __future__ import annotations

from pathlib import Path

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.setup.command_runtime import (
    _build_default_profile_runtime_secrets,
    _build_runtime_secrets_payload,
    render_setup_success,
)
from afkbot.services.setup.contracts import SetupConfig


def test_render_setup_success_in_english(capsys) -> None:
    """Interactive setup should explain the next commands in English."""

    render_setup_success(
        interactive=True,
        prompt_language=PromptLanguage.EN,
        response=None,
    )

    output = capsys.readouterr().out
    assert "AFKBOT setup is complete." in output
    assert "The default profile is ready and saved for future chats." in output
    assert "Next, check local health:" in output
    assert "  afk doctor" in output
    assert "Then open chat and start working with AFKBOT:" in output
    assert "  afk chat" in output
    assert "Inside `afk chat`, describe the task in natural language." in output


def test_render_setup_success_in_russian(capsys) -> None:
    """Interactive setup should explain the next commands in Russian."""

    render_setup_success(
        interactive=True,
        prompt_language=PromptLanguage.RU,
        response=None,
    )

    output = capsys.readouterr().out
    assert "Настройка AFKBOT завершена." in output
    assert "Профиль по умолчанию готов и сохранён для следующих чатов." in output
    assert "Теперь проверьте локальное состояние:" in output
    assert "  afk doctor" in output
    assert "Затем откройте чат и начните работать с AFKBOT:" in output
    assert "  afk chat" in output
    assert "Внутри `afk chat` просто опишите задачу обычным языком." in output


def test_codex_file_backed_setup_secrets_do_not_copy_effective_token(tmp_path: Path) -> None:
    """Setup should store Codex token file metadata without duplicating the OAuth token."""

    config = _setup_config(
        tmp_path,
        runtime_secrets_update={
            "openai_codex_api_key_source": "file",
            "openai_codex_api_key_file": str(tmp_path / "auth.json"),
        },
    )

    runtime_secrets = _build_runtime_secrets_payload(
        config=config,
        existing_runtime_secrets={},
    )
    profile_secrets = _build_default_profile_runtime_secrets(config=config)

    assert runtime_secrets == {
        "openai_codex_api_key_source": "file",
        "openai_codex_api_key_file": str(tmp_path / "auth.json"),
    }
    assert profile_secrets == runtime_secrets
    assert "openai_codex_api_key" not in runtime_secrets


def _setup_config(tmp_path: Path, *, runtime_secrets_update: dict[str, str]) -> SetupConfig:
    return SetupConfig(
        env_file=tmp_path / ".unused",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}",
        prompt_language="en",
        llm_provider="openai-codex",
        chat_model="gpt-5.4",
        llm_thinking_level="medium",
        llm_api_key="effective-codex-token",
        llm_base_url="https://chatgpt.com/backend-api/codex",
        custom_interface="openai",
        llm_proxy_type="none",
        llm_proxy_url="",
        credentials_master_keys="",
        runtime_host="127.0.0.1",
        runtime_port=8081,
        nginx_enabled=False,
        nginx_port=18080,
        nginx_runtime_host="",
        nginx_runtime_public_port=None,
        nginx_runtime_https=False,
        nginx_api_host="",
        nginx_api_public_port=None,
        nginx_api_https=False,
        certbot_email="",
        public_runtime_url="",
        public_chat_api_url="",
        policy_setup_mode="recommended",
        policy_enabled=True,
        policy_preset="medium",
        policy_confirmation_mode="destructive_files",
        policy_capabilities=("memory", "taskflow"),
        policy_file_access_mode="none",
        policy_workspace_scope_mode="profile_only",
        policy_allowed_directories=(),
        policy_shell_sandbox_mode="disabled",
        policy_shell_allowed_commands=(),
        policy_network_mode="deny_all",
        policy_network_allowlist=(),
        default_profile_runtime_config=ProfileRuntimeConfig(
            llm_provider="openai-codex",
            llm_model="gpt-5.4",
        ),
        auto_install_deps=True,
        update_notices_enabled=True,
        runtime_secrets_update=runtime_secrets_update,
    )
