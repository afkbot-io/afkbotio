"""Tests for GitHub Copilot token exchange helpers."""

from __future__ import annotations

import time

from afkbot.services.llm import github_copilot_token as copilot_token


def test_derive_copilot_api_base_url_from_token() -> None:
    """Token metadata should map proxy endpoint to api endpoint."""

    assert (
        copilot_token.derive_copilot_api_base_url_from_token("token;proxy-ep=proxy.example.com;")
        == "https://api.example.com"
    )
    assert (
        copilot_token.derive_copilot_api_base_url_from_token(
            "token;proxy-ep=https://proxy.demo.example.org;",
        )
        == "https://api.demo.example.org"
    )
    assert copilot_token.derive_copilot_api_base_url_from_token("token-only") is None


def test_resolve_copilot_api_token_uses_cache(monkeypatch) -> None:
    """Resolver should cache exchanged token until close to expiry."""

    copilot_token.reset_copilot_api_token_cache()
    calls: list[dict[str, object]] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "token": "copilot-token;proxy-ep=proxy.example.com;",
                "expires_at": int(time.time()) + 3600,
            }

    class _FakeClient:
        def __init__(self, *, timeout, proxy, trust_env) -> None:  # noqa: ANN001
            calls.append(
                {
                    "timeout": timeout,
                    "proxy": proxy,
                    "trust_env": trust_env,
                }
            )

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

        def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
            assert url == copilot_token.COPILOT_TOKEN_URL
            assert headers["Authorization"] == "Bearer gh-token"
            assert headers["X-Github-Api-Version"] == copilot_token.COPILOT_GITHUB_API_VERSION
            return _FakeResponse()

    monkeypatch.setattr(copilot_token.httpx, "Client", _FakeClient)

    first = copilot_token.resolve_copilot_api_token(github_token="gh-token")
    second = copilot_token.resolve_copilot_api_token(github_token="gh-token")

    assert first.token == "copilot-token;proxy-ep=proxy.example.com;"
    assert first.base_url == "https://api.example.com"
    assert second == first
    assert len(calls) == 1
