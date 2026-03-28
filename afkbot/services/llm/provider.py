"""OpenAI-compatible LLM provider entrypoints and provider factory."""

from __future__ import annotations

from collections.abc import Mapping
import sys
import json
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Literal

import httpx

from afkbot.services.llm.contracts import (
    BaseLLMProvider,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from afkbot.services.llm.provider_catalog import LLMProviderId, parse_provider
from afkbot.services.llm.provider_payload_runtime import OpenAICompatiblePayloadRuntime
from afkbot.services.llm.provider_settings import (
    ResolvedProviderDebugInfo,
    describe_provider_debug_info,
    resolve_api_key,
    resolve_base_url,
)
from afkbot.services.llm_timeout_policy import (
    DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
    clamp_llm_request_timeout_sec,
    resolve_llm_request_timeout_sec,
)
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class ModelAPISurface:
    """Provider-specific API surface selected for one model family."""

    kind: Literal["chat_completions", "responses"]


class OpenAICompatibleChatProvider(OpenAICompatiblePayloadRuntime, BaseLLMProvider):
    """HTTP provider for OpenAI-compatible chat-completions and Responses APIs."""

    def __init__(
        self,
        *,
        provider_id: LLMProviderId = LLMProviderId.OPENROUTER,
        model: str,
        api_key: str | None,
        base_url: str,
        proxy_url: str | None = None,
        timeout_sec: float = DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
        debug_diagnostics_enabled: bool = False,
        debug_info: ResolvedProviderDebugInfo | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._model = model.strip()
        self._api_key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._proxy_url = (proxy_url or "").strip()
        self._timeout_sec = clamp_llm_request_timeout_sec(timeout_sec)
        self._debug_diagnostics_enabled = debug_diagnostics_enabled
        self._debug_info = debug_info

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Request one completion from provider or fallback deterministically."""

        if not self._is_configured():
            return self._fallback_response(
                request,
                error_code="llm_provider_not_configured",
            )

        encode_tool_name, decode_tool_name = self._build_tool_name_codec(request.available_tools)
        api_surface = self._resolve_api_surface(self._model)
        request_path = "/responses" if api_surface.kind == "responses" else "/chat/completions"
        timeout_sec = resolve_llm_request_timeout_sec(
            request.request_timeout_sec,
            fallback_sec=self._timeout_sec,
        )
        try:
            if api_surface.kind == "responses":
                payload = self._build_responses_payload(
                    request,
                    encode_tool_name=encode_tool_name,
                )
                response_json = await self._post_responses(payload, timeout_sec=timeout_sec)
                return self._parse_responses_response(
                    response_json,
                    request,
                    decode_tool_name=decode_tool_name,
                )

            payload = self._build_chat_payload(
                request,
                encode_tool_name=encode_tool_name,
            )
            response_json = await self._post_chat(payload, timeout_sec=timeout_sec)
            return self._parse_response(
                response_json,
                request,
                decode_tool_name=decode_tool_name,
            )
        except (TimeoutError, httpx.TimeoutException):
            self._emit_debug_diagnostics(
                stage="timeout",
                path=request_path,
                timeout_sec=timeout_sec,
            )
            return self._fallback_response(request, error_code="llm_timeout")
        except (httpx.TransportError, OSError):
            self._emit_debug_diagnostics(stage="network_error", path=request_path, timeout_sec=timeout_sec)
            return self._fallback_response(request, error_code="llm_provider_network_error")
        except httpx.HTTPStatusError as exc:
            self._emit_debug_diagnostics(
                stage="http_error",
                path=request_path,
                timeout_sec=timeout_sec,
                http_status=exc.response.status_code,
            )
            return self._fallback_http_status(request, exc)
        except (ValueError, json.JSONDecodeError):
            self._emit_debug_diagnostics(stage="invalid_response", path=request_path, timeout_sec=timeout_sec)
            return self._fallback_response(request, error_code="llm_provider_response_invalid")

    def _is_configured(self) -> bool:
        return bool(self._api_key and self._model and self._base_url)

    def _build_chat_payload(
        self,
        request: LLMRequest,
        *,
        encode_tool_name: Callable[[str], str],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": self._build_messages(
                request,
                encode_tool_name=encode_tool_name,
                assistant_tool_call_content_mode=self._assistant_tool_call_content_mode(),
            ),
        }
        tools = self._build_tools(
            request.available_tools,
            encode_tool_name=encode_tool_name,
        )
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _build_responses_payload(
        self,
        request: LLMRequest,
        *,
        encode_tool_name: Callable[[str], str],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "instructions": request.context,
            "input": self._build_responses_input(
                request,
                encode_tool_name=encode_tool_name,
            ),
        }
        tools = self._build_responses_tools(
            request.available_tools,
            encode_tool_name=encode_tool_name,
        )
        if tools:
            payload["tools"] = tools
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        return payload

    async def _post_chat(
        self,
        payload: Mapping[str, object],
        *,
        timeout_sec: float,
    ) -> dict[str, Any]:
        return await self._post_json(path="/chat/completions", payload=payload, timeout_sec=timeout_sec)

    async def _post_responses(
        self,
        payload: Mapping[str, object],
        *,
        timeout_sec: float,
    ) -> dict[str, Any]:
        return await self._post_json(path="/responses", payload=payload, timeout_sec=timeout_sec)

    def _assistant_tool_call_content_mode(self) -> Literal["omit", "null"]:
        if self._provider_id == LLMProviderId.OPENAI:
            return "null"
        return "omit"

    async def _post_json(
        self,
        *,
        path: str,
        payload: Mapping[str, object],
        timeout_sec: float,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        self._emit_debug_diagnostics(stage="request", path=path, timeout_sec=timeout_sec)

        async with httpx.AsyncClient(
            timeout=timeout_sec,
            proxy=self._proxy_url or None,
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
            self._emit_debug_diagnostics(
                stage="response",
                path=path,
                timeout_sec=timeout_sec,
                http_status=response.status_code,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Provider response is not an object")
            return data

    def _emit_debug_diagnostics(
        self,
        *,
        stage: str,
        path: str,
        timeout_sec: float,
        http_status: int | None = None,
    ) -> None:
        """Emit one temporary operator-safe LLM diagnostic line to stderr."""

        if not self._debug_diagnostics_enabled:
            return
        debug_info = self._debug_info
        payload: dict[str, object] = {
            "stage": stage,
            "provider": self._provider_id.value,
            "model": self._model,
            "path": path,
            "timeout_sec": timeout_sec,
            "api_key_present": debug_info.api_key_present if debug_info else bool(self._api_key),
        }
        if http_status is not None:
            payload["http_status"] = http_status
        print(
            "[afkbot.llm.debug] " + json.dumps(payload, ensure_ascii=True, sort_keys=True),
            file=sys.stderr,
        )

    def _resolve_api_surface(self, model: str) -> ModelAPISurface:
        normalized = model.strip().lower()
        if "/" in normalized:
            normalized = normalized.rsplit("/", 1)[-1]
        if self._provider_id != LLMProviderId.OPENAI:
            return ModelAPISurface(kind="chat_completions")
        if normalized.startswith("gpt-5") or normalized.startswith(("o1", "o3", "o4")):
            return ModelAPISurface(kind="responses")
        return ModelAPISurface(kind="chat_completions")

    def _fallback_http_status(self, request: LLMRequest, exc: httpx.HTTPStatusError) -> LLMResponse:
        status_code = exc.response.status_code
        if self._provider_id != LLMProviderId.OPENAI:
            return self._fallback_response(
                request,
                error_code=f"llm_provider_http_{status_code}",
            )
        if status_code in {401, 403}:
            return self._fallback_response(
                request,
                error_code="llm_provider_auth_error",
                message="LLM provider rejected the configured credentials. Check the API key and provider settings.",
            )
        if status_code == 404:
            return self._fallback_response(
                request,
                error_code="llm_provider_model_not_found",
                message="LLM model or endpoint was not found. Check the configured model name and base URL.",
            )
        if status_code == 429:
            return self._fallback_response(
                request,
                error_code="llm_provider_rate_limited",
                message="LLM provider rate-limited this request. Please try again shortly.",
            )
        if 400 <= status_code < 500:
            provider_detail = self._extract_provider_error_detail(exc.response)
            detail_suffix = f" Provider detail: {provider_detail}" if provider_detail else ""
            return self._fallback_response(
                request,
                error_code="llm_provider_invalid_request",
                message=(
                    "LLM request was rejected by the provider."
                    f"{detail_suffix} Check the configured model, API surface, and tool payload."
                ),
            )
        return self._fallback_response(request, error_code="llm_provider_unavailable")

    @staticmethod
    def _extract_provider_error_detail(response: httpx.Response) -> str | None:
        """Return a short provider-supplied 4xx detail when one is available."""

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        raw_text = response.text.strip()
        if not raw_text:
            return None
        compact = " ".join(raw_text.split())
        return compact[:300]


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Build configured LLM provider from runtime settings."""

    provider_id = parse_provider(settings.llm_provider)
    debug_info = describe_provider_debug_info(settings=settings, provider_id=provider_id)
    proxy_url = settings.llm_proxy_url if settings.llm_proxy_type != "none" else None
    return OpenAICompatibleChatProvider(
        provider_id=provider_id,
        model=settings.llm_model,
        api_key=resolve_api_key(settings=settings, provider_id=provider_id),
        base_url=resolve_base_url(settings=settings, provider_id=provider_id),
        proxy_url=proxy_url,
        timeout_sec=float(settings.llm_request_timeout_sec),
        debug_diagnostics_enabled=settings.llm_debug_diagnostics_enabled,
        debug_info=debug_info,
    )


def append_user_message(history: list[LLMMessage], message: str) -> None:
    """Append one user message to provider history."""

    history.append(LLMMessage(role="user", content=message))
