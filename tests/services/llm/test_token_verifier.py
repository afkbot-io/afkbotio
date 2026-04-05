"""Tests for setup-time LLM provider token verifier."""

from __future__ import annotations

import httpx

from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.llm.token_verifier import verify_provider_token


def test_verify_provider_token_success(monkeypatch) -> None:
    """Verifier should accept HTTP 200 as valid token."""

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        return 200, '{"ok":true}'

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.OPENROUTER,
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.status_code == 200


def test_verify_provider_token_treats_rate_limit_as_valid(monkeypatch) -> None:
    """HTTP 429 should be treated as configured token with temporary rate limit."""

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        return 429, '{"error":{"message":"rate limit"}}'

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.OPENAI,
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.status_code == 429


def test_verify_provider_token_invalid_key(monkeypatch) -> None:
    """HTTP 401/403 should return deterministic invalid-token error."""

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        return 401, '{"error":{"message":"bad key"}}'

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.OPENAI,
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    assert result.ok is False
    assert result.error_code == "llm_token_invalid"
    assert result.status_code == 401


def test_verify_provider_token_rejects_invalid_base_url() -> None:
    """Verifier should reject non-http(s) base URLs before request execution."""

    result = verify_provider_token(
        provider_id=LLMProviderId.DEEPSEEK,
        api_key="token",
        base_url="ftp://example.com",
    )

    assert result.ok is False
    assert result.error_code == "llm_base_url_insecure"


def test_verify_provider_token_rejects_plain_http_non_localhost() -> None:
    """Plain HTTP should be blocked for non-local verification endpoints."""

    result = verify_provider_token(
        provider_id=LLMProviderId.DEEPSEEK,
        api_key="token",
        base_url="http://api.deepseek.com",
    )

    assert result.ok is False
    assert result.error_code == "llm_base_url_insecure"


def test_verify_provider_token_allows_http_localhost(monkeypatch) -> None:
    """Plain HTTP localhost should be allowed for local development setups."""

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        return 200, '{"ok":true}'

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.OPENAI,
        api_key="token",
        base_url="http://127.0.0.1:8088/v1",
    )

    assert result.ok is True


def test_verify_provider_token_masks_secret_in_error_message(monkeypatch) -> None:
    """Verifier error output must not include raw API key."""

    secret = "super-secret-key"

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        return 500, f'{{"message":"token {secret} failed"}}'

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.XAI,
        api_key=secret,
        base_url="https://api.x.ai/v1",
    )

    assert result.ok is False
    assert result.error_code == "llm_token_verify_failed"
    assert result.reason is not None
    assert secret not in result.reason


def test_verify_provider_token_reports_network_error(monkeypatch) -> None:
    """Network-level errors should map to deterministic verification failure."""

    def _fake_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        _ = request, proxy_url, timeout_sec
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fake_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.QWEN,
        api_key="token",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    assert result.ok is False
    assert result.error_code == "llm_token_verify_network_error"


def test_verify_provider_token_skips_http_probe_for_openai_codex(monkeypatch) -> None:
    """OpenAI Codex OAuth verification should skip HTTP verify-path probing."""

    def _fail_execute_request(*, request, proxy_url, timeout_sec):  # noqa: ANN001
        del request, proxy_url, timeout_sec
        raise AssertionError("HTTP verifier must be skipped for openai-codex")

    monkeypatch.setattr("afkbot.services.llm.token_verifier._execute_request", _fail_execute_request)
    result = verify_provider_token(
        provider_id=LLMProviderId.OPENAI_CODEX,
        api_key="oauth-token",
        base_url="",
    )

    assert result.ok is True
    assert result.error_code is None


def test_verify_provider_token_uses_github_copilot_exchange(monkeypatch) -> None:
    """GitHub Copilot verification should use token exchange instead of verify-path GET."""

    def _fake_exchange(*, github_token, proxy_url, timeout_sec):  # noqa: ANN001
        assert github_token == "gh-oauth-token"
        assert proxy_url is None
        assert timeout_sec == 10.0
        return object()

    monkeypatch.setattr(
        "afkbot.services.llm.token_verifier.resolve_copilot_api_token",
        _fake_exchange,
    )
    result = verify_provider_token(
        provider_id=LLMProviderId.GITHUB_COPILOT,
        api_key="gh-oauth-token",
        base_url="",
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.status_code == 200
