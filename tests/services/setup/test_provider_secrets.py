"""Tests for provider secret resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path

from afkbot.cli.presentation.setup_prompts import PromptLanguage
from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.llm.token_file_sources import (
    TOKEN_SOURCE_FILE,
    TOKEN_SOURCE_SECRET,
    FileBackedProviderToken,
)
from afkbot.services.setup.provider_secrets import (
    _MiniMaxPortalDeviceAuthResult,
    _load_local_codex_access_token,
    _resolve_interactive_provider_credential_with_metadata,
    resolve_profile_provider_api_key,
)


def test_resolve_profile_provider_api_key_accepts_minimax_region_override() -> None:
    """Explicit MiniMax region should set preferred provider base URL and persisted metadata."""

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.MINIMAX_PORTAL,
        provider_name="minimax-portal",
        interactive=False,
        defaults={},
        lang=PromptLanguage.EN,
        provider_api_key="oauth-token",
        minimax_region="cn",
    )

    assert result.effective_api_key == "oauth-token"
    assert result.preferred_base_url == "https://api.minimaxi.com/v1"
    assert result.runtime_secrets_update["minimax_portal_api_key"] == "oauth-token"
    assert result.runtime_secrets_update["minimax_portal_region"] == "cn"
    assert "llm_api_key" not in result.runtime_secrets_update


def test_load_local_codex_access_token_reads_codex_home(monkeypatch, tmp_path: Path) -> None:
    """Codex token loader should read access_token from CODEX_HOME/auth.json."""

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": "local-codex-token"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    token = _load_local_codex_access_token()

    assert token == "local-codex-token"


def test_load_local_codex_access_token_ignores_expired_jwt(monkeypatch, tmp_path: Path) -> None:
    """Expired ChatGPT OAuth access tokens should not be auto-reused during setup."""

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "header.eyJleHAiOjEwfQ.signature",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("afkbot.services.llm.token_file_sources.Path.home", lambda: tmp_path)

    token = _load_local_codex_access_token()

    assert token == ""


def test_openai_codex_interactive_prefers_detected_local_token_file(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    """Interactive OpenAI Codex setup should store the local token file reference."""

    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.discover_local_openai_codex_access_token_file",
        lambda: FileBackedProviderToken(token="detected", path=auth_path),
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.prompt_confirm", lambda **kwargs: True
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._prompt_hidden_credential_input",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("manual prompt should not be called")
        ),
    )

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.OPENAI_CODEX,
        provider_name="openai-codex",
        interactive=True,
        defaults={},
        lang=PromptLanguage.EN,
    )

    assert result.effective_api_key == "detected"
    assert result.runtime_secrets_update == {
        "openai_codex_api_key_file": str(auth_path.resolve(strict=False)),
        "openai_codex_api_key_source": TOKEN_SOURCE_FILE,
    }
    assert str(auth_path) not in capsys.readouterr().out


def test_openai_codex_interactive_runs_codex_login_when_requested(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    """Interactive OpenAI Codex setup should store the refreshed Codex auth file reference."""

    auth_path = tmp_path / "auth.json"
    discovered = iter(
        [None, FileBackedProviderToken(token="fresh-from-codex-login", path=auth_path)]
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.discover_local_openai_codex_access_token_file",
        lambda: next(discovered),
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.prompt_confirm", lambda **kwargs: True
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._run_codex_login_and_load_token",
        lambda **kwargs: "fresh-from-codex-login",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._prompt_hidden_credential_input",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("manual prompt should not be called")
        ),
    )

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.OPENAI_CODEX,
        provider_name="openai-codex",
        interactive=True,
        defaults={},
        lang=PromptLanguage.EN,
    )

    assert result.effective_api_key == "fresh-from-codex-login"
    assert result.runtime_secrets_update == {
        "openai_codex_api_key_file": str(auth_path.resolve(strict=False)),
        "openai_codex_api_key_source": TOKEN_SOURCE_FILE,
    }
    assert str(auth_path) not in capsys.readouterr().out


def test_openai_codex_interactive_falls_back_to_manual_input_when_login_unavailable(
    monkeypatch,
) -> None:
    """Interactive OpenAI Codex setup should fallback to hidden token prompt when login fails."""

    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.discover_local_openai_codex_access_token_file",
        lambda: None,
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.prompt_confirm", lambda **kwargs: True
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._run_codex_login_and_load_token",
        lambda **kwargs: "",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._prompt_hidden_credential_input",
        lambda **kwargs: "manual-token",
    )

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.OPENAI_CODEX,
        provider_name="openai-codex",
        interactive=True,
        defaults={},
        lang=PromptLanguage.EN,
    )

    assert result.effective_api_key == "manual-token"
    assert result.runtime_secrets_update["openai_codex_api_key"] == "manual-token"
    assert result.runtime_secrets_update["openai_codex_api_key_source"] == TOKEN_SOURCE_SECRET


def test_openai_codex_key_file_uses_file_backed_runtime_metadata(tmp_path: Path) -> None:
    """Codex --llm-api-key-file should persist file metadata instead of copying the token."""

    token_file = tmp_path / "codex-token.txt"
    token_file.write_text("codex-file-token\n", encoding="utf-8")

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.OPENAI_CODEX,
        provider_name="openai-codex",
        interactive=False,
        defaults={},
        lang=PromptLanguage.EN,
        key_file=token_file,
    )

    assert result.effective_api_key == "codex-file-token"
    assert result.runtime_secrets_update == {
        "openai_codex_api_key_file": str(token_file.resolve(strict=False)),
        "openai_codex_api_key_source": TOKEN_SOURCE_FILE,
    }


def test_openai_codex_current_file_source_does_not_fall_back_to_stale_secret(
    tmp_path: Path,
) -> None:
    """Codex source=file should not reuse copied secrets when the file is unavailable."""

    result = resolve_profile_provider_api_key(
        provider_id=LLMProviderId.OPENAI_CODEX,
        provider_name="openai-codex",
        interactive=False,
        defaults={},
        lang=PromptLanguage.EN,
        current_runtime_secrets={
            "openai_codex_api_key_source": TOKEN_SOURCE_FILE,
            "openai_codex_api_key_file": str(tmp_path / "missing-auth.json"),
            "openai_codex_api_key": "stale-copied-token",
            "llm_api_key": "stale-generic-token",
        },
        required=False,
    )

    assert result.effective_api_key == ""
    assert result.runtime_secrets_update == {}


def test_minimax_interactive_returns_oauth_metadata(monkeypatch) -> None:
    """MiniMax interactive OAuth flow should return refresh and expiry metadata."""

    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._prompt_minimax_region", lambda **_: "cn"
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets.prompt_confirm", lambda **kwargs: True
    )
    monkeypatch.setattr(
        "afkbot.services.setup.provider_secrets._run_minimax_portal_device_code_flow",
        lambda **kwargs: _MiniMaxPortalDeviceAuthResult(
            access_token="at-1",
            refresh_token="rt-1",
            expires_at_epoch_sec=1_700_003_600,
            region="cn",
            resource_url="https://api.minimaxi.com/v1",
        ),
    )

    resolved = _resolve_interactive_provider_credential_with_metadata(
        provider_id=LLMProviderId.MINIMAX_PORTAL,
        existing_key="",
        lang=PromptLanguage.EN,
    )

    assert resolved.token == "at-1"
    assert resolved.preferred_base_url == "https://api.minimaxi.com/v1"
    assert resolved.runtime_secrets_update == {
        "minimax_portal_refresh_token": "rt-1",
        "minimax_portal_token_expires_at": "1700003600",
        "minimax_portal_region": "cn",
        "minimax_portal_resource_url": "https://api.minimaxi.com/v1",
    }
