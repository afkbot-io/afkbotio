"""Token verification helper for setup-time provider setup."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin, urlparse
from urllib.request import Request

import httpx

from afkbot.services.llm.github_copilot_token import resolve_copilot_api_token
from afkbot.services.llm.provider_catalog import (
    LLMProviderId,
    get_provider_spec,
    provider_token_verify_mode,
)


@dataclass(slots=True, frozen=True)
class TokenVerificationResult:
    """Deterministic result returned by provider token verifier."""

    ok: bool
    error_code: str | None
    reason: str | None
    status_code: int | None


def verify_provider_token(
    *,
    provider_id: LLMProviderId,
    api_key: str,
    base_url: str,
    model: str | None = None,
    proxy_url: str | None = None,
    timeout_sec: float = 10.0,
) -> TokenVerificationResult:
    """Verify provider API key using provider-specific verification endpoint."""

    normalized_key = api_key.strip()
    if not normalized_key:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_missing",
            reason="LLM provider credential is required.",
            status_code=None,
        )

    verify_mode = provider_token_verify_mode(provider_id)
    if verify_mode == "skip":
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=None)
    if verify_mode == "openai_codex_responses_post":
        return _verify_openai_codex_token(
            api_key=normalized_key,
            base_url=base_url,
            model=model or "gpt-5.3-codex",
            proxy_url=proxy_url,
            timeout_sec=timeout_sec,
        )
    if verify_mode == "github_copilot_exchange":
        try:
            _ = resolve_copilot_api_token(
                github_token=normalized_key,
                proxy_url=(proxy_url or "").strip() or None,
                timeout_sec=timeout_sec,
            )
        except httpx.TimeoutException:
            return TokenVerificationResult(
                ok=False,
                error_code="llm_token_verify_timeout",
                reason="LLM token verification timed out.",
                status_code=None,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                return TokenVerificationResult(
                    ok=False,
                    error_code="llm_token_invalid",
                    reason=f"Provider rejected credentials (HTTP {status}).",
                    status_code=status,
                )
            return TokenVerificationResult(
                ok=False,
                error_code="llm_token_verify_failed",
                reason=f"LLM token verification failed (HTTP {status}).",
                status_code=status,
            )
        except (httpx.RequestError, OSError) as exc:
            return TokenVerificationResult(
                ok=False,
                error_code="llm_token_verify_network_error",
                reason=f"LLM token verification failed due to network error: {exc}",
                status_code=None,
            )
        except ValueError as exc:
            return TokenVerificationResult(
                ok=False,
                error_code="llm_token_verify_failed",
                reason=f"LLM token verification failed: {exc}",
                status_code=None,
            )
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=200)

    normalized_base_url = base_url.strip().rstrip("/")
    if not _is_allowed_base_url(normalized_base_url):
        return TokenVerificationResult(
            ok=False,
            error_code="llm_base_url_insecure",
            reason="LLM base URL must use https:// (http:// is allowed only for localhost).",
            status_code=None,
        )

    spec = get_provider_spec(provider_id)
    verify_url = urljoin(f"{normalized_base_url}/", spec.verify_path.lstrip("/"))
    headers = {
        "Authorization": f"Bearer {normalized_key}",
        "Accept": "application/json",
    }
    request = Request(url=verify_url, headers=headers, method="GET")

    try:
        status_code, body_text = _execute_request(
            request=request,
            proxy_url=(proxy_url or "").strip() or None,
            timeout_sec=timeout_sec,
        )
    except httpx.TimeoutException:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_timeout",
            reason="LLM token verification timed out.",
            status_code=None,
        )
    except httpx.RequestError as exc:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_network_error",
            reason=f"LLM token verification failed due to network error: {exc}",
            status_code=None,
        )
    except TimeoutError:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_timeout",
            reason="LLM token verification timed out.",
            status_code=None,
        )
    except OSError as exc:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_network_error",
            reason=f"LLM token verification failed due to network error: {exc}",
            status_code=None,
        )

    if status_code in {200, 204, 429}:
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=status_code)
    if status_code in {401, 403}:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_invalid",
            reason=f"Provider rejected credentials (HTTP {status_code}).",
            status_code=status_code,
        )
    if status_code == 404:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_verify_endpoint_not_found",
            reason=f"Verification endpoint not found (HTTP 404): {verify_url}",
            status_code=status_code,
        )

    error_message = _extract_safe_error_message(body_text=body_text, api_key=normalized_key)
    return TokenVerificationResult(
        ok=False,
        error_code="llm_token_verify_failed",
        reason=f"LLM token verification failed (HTTP {status_code}): {error_message}",
        status_code=status_code,
    )


def _execute_request(
    *,
    request: Request,
    proxy_url: str | None,
    timeout_sec: float,
) -> tuple[int, str]:
    request_content = cast("str | bytes | Iterable[bytes] | None", request.data)
    with httpx.Client(timeout=timeout_sec, proxy=proxy_url, trust_env=False) as client:
        response = client.request(
            method=request.get_method(),
            url=request.full_url,
            headers=dict(request.header_items()),
            content=request_content,
        )
        status_code = int(response.status_code)
        body_text = response.text
        return status_code, body_text


def _verify_openai_codex_token(
    *,
    api_key: str,
    base_url: str,
    model: str,
    proxy_url: str | None,
    timeout_sec: float,
) -> TokenVerificationResult:
    normalized_base_url = base_url.strip().rstrip("/")
    if not _is_allowed_base_url(normalized_base_url):
        return TokenVerificationResult(
            ok=False,
            error_code="llm_base_url_insecure",
            reason="LLM base URL must use https:// (http:// is allowed only for localhost).",
            status_code=None,
        )
    body = json.dumps(
        {
            "model": model.strip() or "gpt-5.3-codex",
            "instructions": "Token verification probe.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Reply with ok.",
                        }
                    ],
                }
            ],
            "store": False,
            "stream": True,
        },
        ensure_ascii=True,
    ).encode("utf-8")
    request = Request(
        url=f"{normalized_base_url}/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
        },
        data=body,
        method="POST",
    )
    try:
        status_code, body_text = _execute_request(
            request=request,
            proxy_url=(proxy_url or "").strip() or None,
            timeout_sec=timeout_sec,
        )
    except httpx.TimeoutException:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_timeout",
            reason="LLM token verification timed out.",
            status_code=None,
        )
    except httpx.RequestError as exc:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_network_error",
            reason=f"LLM token verification failed due to network error: {exc}",
            status_code=None,
        )
    except TimeoutError:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_timeout",
            reason="LLM token verification timed out.",
            status_code=None,
        )
    except OSError as exc:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_network_error",
            reason=f"LLM token verification failed due to network error: {exc}",
            status_code=None,
        )
    if status_code in {200, 204}:
        return TokenVerificationResult(ok=True, error_code=None, reason=None, status_code=status_code)
    if status_code == 429:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_verify_rate_limited",
            reason=(
                "OpenAI Codex rate-limited token verification before auth could be confirmed. "
                "Retry the verification in a few moments."
            ),
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return TokenVerificationResult(
            ok=False,
            error_code="llm_token_invalid",
            reason=(
                "OpenAI Codex rejected the configured ChatGPT OAuth token. "
                "Run `codex login` again or paste a fresh access token."
            ),
            status_code=status_code,
        )
    error_message = _extract_safe_error_message(body_text=body_text, api_key=api_key)
    return TokenVerificationResult(
        ok=False,
        error_code="llm_token_verify_failed",
        reason=f"LLM token verification failed (HTTP {status_code}): {error_message}",
        status_code=status_code,
    )


def _extract_safe_error_message(*, body_text: str, api_key: str) -> str:
    if not body_text:
        return "No response body"
    normalized = body_text.strip()
    if not normalized:
        return "No response body"
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return _sanitize_secret(normalized[:200], api_key=api_key)

    if isinstance(payload, dict):
        raw = payload.get("error")
        if isinstance(raw, dict):
            message = raw.get("message")
            if isinstance(message, str) and message.strip():
                return _sanitize_secret(message.strip()[:200], api_key=api_key)
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return _sanitize_secret(message.strip()[:200], api_key=api_key)
    return "Unexpected provider error payload"


def _sanitize_secret(value: str, *, api_key: str) -> str:
    sanitized = value
    if api_key:
        sanitized = sanitized.replace(api_key, "***")
    return sanitized


def _is_allowed_base_url(value: str) -> bool:
    parsed = urlparse(value)
    if not parsed.netloc:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def token_expired_or_expiring_soon(*, token: str, now_epoch: int | None = None, skew_sec: int = 300) -> bool:
    """Return whether unsigned JWT `exp` is already stale or near expiry."""

    parts = token.split(".")
    if len(parts) != 3:
        return False
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    exp_raw = payload.get("exp")
    if isinstance(exp_raw, bool):
        return False
    if not isinstance(exp_raw, (int, float)):
        return False
    current = int(time.time()) if now_epoch is None else int(now_epoch)
    return int(exp_raw) <= current + max(0, int(skew_sec))
