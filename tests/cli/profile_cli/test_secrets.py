"""Secrets and provider-key flow tests for profile CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.llm.token_verifier import TokenVerificationResult
from afkbot.services.profile_runtime import get_profile_runtime_secrets_service
from afkbot.settings import get_settings
from tests.cli.profile_cli._harness import _prepare_env


def test_profile_add_and_profile_secrets_commands_manage_local_provider_keys(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile CLI should persist encrypted provider secrets outside runtime config JSON."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "ops",
            "--name",
            "Ops",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--provider-api-key",
            "openai-profile-key",
            "--skip-llm-token-verify",
            "--brave-api-key",
            "brave-profile-key",
        ],
    )
    show_result = runner.invoke(app, ["profile", "show", "ops", "--json"])
    set_result = runner.invoke(
        app,
        [
            "profile",
            "secrets",
            "set",
            "ops",
            "--llm-api-key",
            "fallback-key",
        ],
    )
    clear_result = runner.invoke(
        app,
        [
            "profile",
            "secrets",
            "clear",
            "ops",
            "--provider-api-key",
            "--brave-api-key",
        ],
    )
    clear_all_result = runner.invoke(app, ["profile", "secrets", "clear", "ops", "--all"])

    # Assert
    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["profile"]["effective_runtime"]["provider_api_key_configured"] is True
    assert add_payload["profile"]["effective_runtime"]["brave_api_key_configured"] is True
    assert add_payload["profile"]["runtime_secrets"]["configured_fields"] == [
        "brave_api_key",
        "openai_api_key",
    ]
    assert add_payload["profile"]["runtime_secrets"]["has_profile_secrets"] is True
    assert add_payload["profile"]["runtime_secrets_path"] == "profiles/ops/.system/agent_secrets.json"

    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["profile"]["runtime_config"]["llm_model"] == "gpt-4o-mini"
    assert show_payload["profile"]["runtime_secrets"]["configured_fields"] == [
        "brave_api_key",
        "openai_api_key",
    ]
    assert "openai-profile-key" not in show_result.stdout
    assert "brave-profile-key" not in show_result.stdout

    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.stdout)
    assert set(set_payload["runtime_secrets"]["configured_fields"]) == {
        "brave_api_key",
        "llm_api_key",
        "openai_api_key",
    }

    assert clear_result.exit_code == 0
    clear_payload = json.loads(clear_result.stdout)
    assert clear_payload["runtime_secrets"]["configured_fields"] == ["llm_api_key"]

    assert clear_all_result.exit_code == 0
    clear_all_payload = json.loads(clear_all_result.stdout)
    assert clear_all_payload["runtime_secrets"]["configured_fields"] == []
    assert clear_all_payload["runtime_secrets"]["has_profile_secrets"] is False


def test_profile_add_interactive_prompts_for_provider_api_key(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive profile add should prompt for provider API key and persist it locally."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.profile_mutation_support.verify_provider_token",
        lambda **_: TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200),
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--id",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--llm-base-url",
            "https://api.openai.com/v1",
            "--llm-proxy-type",
            "none",
            "--policy-enabled",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
        input="profile-openai-key\n\n",
    )
    show_result = runner.invoke(app, ["profile", "show", "support", "--json"])

    # Assert
    assert result.exit_code == 0
    assert "OpenAI API key" in result.output
    assert "Profile `support` created successfully." in result.output

    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["profile"]["runtime_secrets"]["configured_fields"] == [
        "llm_api_key",
        "openai_api_key",
    ]
    assert show_payload["profile"]["effective_runtime"]["provider_api_key_configured"] is True


def test_profile_add_interactive_keeps_existing_global_key_without_copying_it_locally(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive profile add should allow keeping an existing global key without duplicating it locally."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setenv("AFKBOT_OPENAI_API_KEY", "global-openai-key")
    monkeypatch.setattr(
        "afkbot.cli.commands.profile_mutation_support.verify_provider_token",
        lambda **_: TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200),
    )
    get_settings.cache_clear()
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--id",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--llm-base-url",
            "https://api.openai.com/v1",
            "--llm-proxy-type",
            "none",
            "--policy-enabled",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
        input="\n\n",
    )
    show_result = runner.invoke(app, ["profile", "show", "support", "--json"])

    # Assert
    assert result.exit_code == 0
    assert "currently configured" in result.output

    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["profile"]["runtime_secrets"]["configured_fields"] == []
    assert show_payload["profile"]["effective_runtime"]["provider_api_key_configured"] is True


def test_profile_update_interactive_prompts_for_provider_api_key_and_persists_change(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive profile update should prompt for provider token and persist a changed value."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.profile_mutation_support.verify_provider_token",
        lambda **_: TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200),
    )
    runner = CliRunner()
    create_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--provider-api-key",
            "old-openai-key",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
    )
    assert create_result.exit_code == 0

    # Act
    result = runner.invoke(
        app,
        [
            "profile",
            "update",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--llm-base-url",
            "https://api.openai.com/v1",
            "--llm-proxy-type",
            "none",
            "--policy-enabled",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
        input="new-openai-key\n\n",
    )

    # Assert
    assert result.exit_code == 0
    assert "OpenAI API key" in result.output

    settings = get_settings()
    runtime_secrets = get_profile_runtime_secrets_service(settings).load("support")
    assert runtime_secrets["openai_api_key"] == "new-openai-key"
    assert runtime_secrets["llm_api_key"] == "new-openai-key"


def test_profile_add_verifies_provider_api_key_over_generic_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile add should verify against the newly chosen provider key, not a stale generic fallback."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    captured: list[str] = []

    def _verify_provider_token(**kwargs: object) -> TokenVerificationResult:
        captured.append(str(kwargs["api_key"]))
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200)

    monkeypatch.setattr(
        "afkbot.cli.commands.profile_mutation_support.verify_provider_token",
        _verify_provider_token,
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--llm-api-key",
            "stale-generic-key",
            "--provider-api-key",
            "fresh-provider-key",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
    )

    # Assert
    assert result.exit_code == 0
    assert captured == ["fresh-provider-key"]


def test_profile_update_verifies_provider_api_key_over_stale_saved_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile update should verify against the new provider key, not a stale saved llm_api_key."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    captured: list[str] = []

    def _verify_provider_token(**kwargs: object) -> TokenVerificationResult:
        captured.append(str(kwargs["api_key"]))
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200)

    monkeypatch.setattr(
        "afkbot.cli.commands.profile_mutation_support.verify_provider_token",
        _verify_provider_token,
    )
    runner = CliRunner()
    create_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "support",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--provider-api-key",
            "old-provider-key",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
    )
    assert create_result.exit_code == 0
    captured.clear()

    secrets = get_profile_runtime_secrets_service(get_settings())
    secrets.merge("support", {"llm_api_key": "stale-generic-key"})

    # Act
    update_result = runner.invoke(
        app,
        [
            "profile",
            "update",
            "support",
            "--yes",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--provider-api-key",
            "fresh-provider-key",
            "--policy-enabled",
            "--policy-preset",
            "simple",
            "--policy-capability",
            "files",
            "--policy-network-host",
            "*",
        ],
    )

    # Assert
    assert update_result.exit_code == 0
    assert captured == ["fresh-provider-key"]
