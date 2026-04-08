"""OpenAI-compatible LLM provider entrypoints and provider factory."""

from __future__ import annotations

from collections.abc import Mapping
import sys
import json
import time
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
from afkbot.services.llm.minimax_portal_oauth import (
    MINIMAX_PORTAL_OAUTH_CLIENT_ID,
    MINIMAX_PORTAL_PROVIDER_BASE_URL_CN,
    MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL,
    extract_minimax_oauth_error_message,
    infer_minimax_portal_region_from_base_url,
    minimax_portal_oauth_base_url_for_region,
    minimax_portal_provider_base_url_for_region,
    normalize_minimax_portal_region,
    normalize_minimax_portal_resource_url,
    parse_minimax_portal_token_payload,
)
from afkbot.services.llm.provider_payload_runtime import OpenAICompatiblePayloadRuntime
from afkbot.services.llm.github_copilot_token import (
    build_copilot_ide_headers,
    resolve_copilot_api_token,
)
from afkbot.services.llm.tool_name_codec import build_tool_name_codec
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
        minimax_portal_refresh_token: str | None = None,
        minimax_portal_token_expires_at: str | None = None,
        minimax_portal_resource_url: str | None = None,
        minimax_portal_region: str | None = None,
        runtime_secrets_update_hook: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._model = model.strip()
        self._api_key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._proxy_url = (proxy_url or "").strip()
        self._timeout_sec = clamp_llm_request_timeout_sec(timeout_sec)
        self._debug_diagnostics_enabled = debug_diagnostics_enabled
        self._debug_info = debug_info
        self._minimax_portal_refresh_token = (minimax_portal_refresh_token or "").strip()
        self._minimax_portal_token_expires_at = (minimax_portal_token_expires_at or "").strip()
        self._minimax_portal_resource_url = (minimax_portal_resource_url or "").strip()
        self._minimax_portal_region = normalize_minimax_portal_region(
            minimax_portal_region,
            default=infer_minimax_portal_region_from_base_url(self._base_url),
        )
        self._runtime_secrets_update_hook = runtime_secrets_update_hook

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Request one completion from provider or fallback deterministically."""

        if not self._is_configured():
            return self._fallback_response(
                request,
                error_code="llm_provider_not_configured",
            )

        encode_tool_name, decode_tool_name = build_tool_name_codec(
            visible_tool_names=(definition.name for definition in request.available_tools),
            historical_tool_names=(
                call.name
                for item in request.history
                for call in item.tool_calls
            ),
        )
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
        input_items = self._build_responses_input(
            request,
            encode_tool_name=encode_tool_name,
        )
        if self._provider_id == LLMProviderId.OPENAI_CODEX:
            input_items = self._filter_codex_stateless_replay_items(
                input_items,
                store_enabled=False,
            )
        payload: dict[str, object] = {
            "model": self._model,
            "instructions": request.context,
            "input": input_items,
        }
        tools = self._build_responses_tools(
            request.available_tools,
            encode_tool_name=encode_tool_name,
        )
        if tools:
            payload["tools"] = tools
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        if self._provider_id == LLMProviderId.OPENAI_CODEX:
            # Codex backend requires explicit opt-out from transcript persistence.
            payload["store"] = False
            # Codex backend currently serves Responses via SSE-only transport.
            payload["stream"] = True
        return payload

    @staticmethod
    def _filter_codex_stateless_replay_items(
        items: list[dict[str, object]],
        *,
        store_enabled: bool,
    ) -> list[dict[str, object]]:
        """Drop replay-only item types that Codex rejects when `store=false`."""

        if store_enabled:
            return list(items)
        filtered: list[dict[str, object]] = []
        for item in items:
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "reasoning":
                continue
            filtered.append(item)
        return filtered

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
        if self._provider_id in {
            LLMProviderId.OPENAI,
            LLMProviderId.OPENAI_CODEX,
            LLMProviderId.GITHUB_COPILOT,
        }:
            return "null"
        return "omit"

    async def _post_json(
        self,
        *,
        path: str,
        payload: Mapping[str, object],
        timeout_sec: float,
    ) -> dict[str, Any]:
        if self._provider_id == LLMProviderId.MINIMAX_PORTAL:
            self._apply_minimax_region_default_base_url()
            await self._maybe_refresh_minimax_portal_token(timeout_sec=timeout_sec)
        resolved_base_url = self._base_url
        resolved_api_key = self._api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._provider_id == LLMProviderId.GITHUB_COPILOT:
            copilot_auth = resolve_copilot_api_token(
                github_token=self._api_key,
                proxy_url=self._proxy_url or None,
                timeout_sec=timeout_sec,
            )
            resolved_base_url = copilot_auth.base_url.rstrip("/")
            resolved_api_key = copilot_auth.token
            headers.update(build_copilot_ide_headers())
        headers["Authorization"] = f"Bearer {resolved_api_key}"
        url = f"{resolved_base_url}{path}"
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
            if self._provider_id == LLMProviderId.OPENAI_CODEX and path == "/responses":
                data = self._decode_openai_codex_sse_response(response.text)
            else:
                data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Provider response is not an object")
            return data

    @staticmethod
    def _decode_openai_codex_sse_response(body: str) -> dict[str, object]:
        """Decode one Codex SSE response body into the final Responses object."""

        compact = body.strip()
        if not compact:
            raise ValueError("Codex response is empty")
        if compact.startswith("{"):
            parsed = json.loads(compact)
            if not isinstance(parsed, dict):
                raise ValueError("Codex response is not a JSON object")
            return parsed

        latest_response: dict[str, object] | None = None
        completed_response: dict[str, object] | None = None
        collected_output_items: dict[str, tuple[int, int, dict[str, object]]] = {}
        data_lines: list[str] = []

        def _consume_event_data(lines: list[str]) -> tuple[dict[str, object] | None, bool]:
            if not lines:
                return None, False
            joined = "\n".join(lines).strip()
            if not joined or joined == "[DONE]":
                return None, joined == "[DONE]"
            try:
                payload = json.loads(joined)
            except json.JSONDecodeError as exc:
                raise ValueError("Codex SSE response contains invalid JSON event payload") from exc
            if not isinstance(payload, dict):
                return None, False
            item_obj = payload.get("item")
            if isinstance(item_obj, dict):
                event_type = str(payload.get("type") or "").strip()
                if event_type == "response.output_item.done":
                    item_id_raw = item_obj.get("id")
                    item_id = item_id_raw.strip() if isinstance(item_id_raw, str) else ""
                    if item_id:
                        output_index_raw = payload.get("output_index")
                        sequence_raw = payload.get("sequence_number")
                        try:
                            output_index = int(output_index_raw)
                        except (TypeError, ValueError):
                            output_index = len(collected_output_items)
                        try:
                            sequence_number = int(sequence_raw)
                        except (TypeError, ValueError):
                            sequence_number = output_index
                        collected_output_items[item_id] = (
                            output_index,
                            sequence_number,
                            dict(item_obj),
                        )
            response_obj = payload.get("response")
            if isinstance(response_obj, dict):
                event_type = str(payload.get("type") or "").strip()
                is_completed = event_type == "response.completed" or str(response_obj.get("status") or "").strip() == "completed"
                return response_obj, is_completed
            return None, False

        for raw_line in compact.splitlines():
            line = raw_line.rstrip("\r")
            if not line:
                response_obj, is_completed = _consume_event_data(data_lines)
                data_lines = []
                if response_obj is not None:
                    latest_response = response_obj
                    if is_completed:
                        completed_response = response_obj
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        response_obj, is_completed = _consume_event_data(data_lines)
        if response_obj is not None:
            latest_response = response_obj
            if is_completed:
                completed_response = response_obj

        resolved = completed_response or latest_response
        if resolved is None:
            raise ValueError("Codex SSE response did not include any response payload")
        output = resolved.get("output")
        if (not isinstance(output, list) or not output) and collected_output_items:
            resolved = dict(resolved)
            resolved["output"] = [
                item
                for _, _, item in sorted(
                    collected_output_items.values(),
                    key=lambda entry: (entry[0], entry[1]),
                )
            ]
        return resolved

    async def _maybe_refresh_minimax_portal_token(self, *, timeout_sec: float) -> None:
        """Refresh MiniMax OAuth access token when expiry metadata indicates staleness."""

        refresh_token = self._minimax_portal_refresh_token.strip()
        if not refresh_token:
            return
        expires_at_epoch = self._resolve_minimax_portal_expiry_epoch()
        now_epoch = int(time.time())
        # Refresh slightly early to avoid race between token expiry and in-flight requests.
        if expires_at_epoch is None or (expires_at_epoch - now_epoch) > 60:
            self._apply_minimax_region_default_base_url()
            return

        region = self._minimax_portal_region
        oauth_base_url = minimax_portal_oauth_base_url_for_region(region)
        refresh_url = f"{oauth_base_url}/oauth/token"
        try:
            async with httpx.AsyncClient(
                timeout=timeout_sec,
                proxy=self._proxy_url or None,
                trust_env=False,
            ) as client:
                response = await client.post(
                    refresh_url,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    data={
                        "grant_type": "refresh_token",
                        "client_id": MINIMAX_PORTAL_OAUTH_CLIENT_ID,
                        "refresh_token": refresh_token,
                    },
                )
                body_text = response.text.strip()
                token_payload: object = {}
                if body_text:
                    try:
                        token_payload = response.json()
                    except ValueError:
                        token_payload = {}
                if not response.is_success:
                    message = extract_minimax_oauth_error_message(token_payload)
                    raise ValueError(
                        message or f"MiniMax OAuth refresh failed (HTTP {response.status_code})."
                    )
                token = parse_minimax_portal_token_payload(
                    token_payload,
                    default_refresh_token=refresh_token,
                    now_epoch_sec=now_epoch,
                )
        except (httpx.RequestError, OSError, ValueError):
            self._emit_debug_diagnostics(
                stage="oauth_refresh_failed",
                path="/oauth/token",
                timeout_sec=timeout_sec,
            )
            return

        self._api_key = token.access_token
        self._minimax_portal_refresh_token = token.refresh_token
        self._minimax_portal_token_expires_at = str(token.expires_at_epoch_sec)
        normalized_resource_url = normalize_minimax_portal_resource_url(token.resource_url)
        if normalized_resource_url:
            self._minimax_portal_resource_url = normalized_resource_url
        self._apply_minimax_region_default_base_url()
        self._persist_minimax_runtime_secrets()

    def _resolve_minimax_portal_expiry_epoch(self) -> int | None:
        raw = self._minimax_portal_token_expires_at.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None

    def _apply_minimax_region_default_base_url(self) -> None:
        """Keep default MiniMax base URL aligned with selected region without overriding custom URLs."""

        normalized_current = self._base_url.rstrip("/")
        if normalized_current in {
            MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL.rstrip("/"),
            MINIMAX_PORTAL_PROVIDER_BASE_URL_CN.rstrip("/"),
        }:
            self._base_url = minimax_portal_provider_base_url_for_region(self._minimax_portal_region).rstrip(
                "/"
            )

    def _persist_minimax_runtime_secrets(self) -> None:
        if self._runtime_secrets_update_hook is None:
            return
        updates: dict[str, str] = {
            "minimax_portal_api_key": self._api_key,
            "minimax_portal_refresh_token": self._minimax_portal_refresh_token,
            "minimax_portal_token_expires_at": self._minimax_portal_token_expires_at,
            "minimax_portal_region": self._minimax_portal_region,
        }
        if self._minimax_portal_resource_url:
            updates["minimax_portal_resource_url"] = self._minimax_portal_resource_url
        try:
            self._runtime_secrets_update_hook(updates)
        except (OSError, ValueError):
            self._emit_debug_diagnostics(
                stage="oauth_refresh_persist_failed",
                path="/oauth/token",
                timeout_sec=self._timeout_sec,
            )

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
        if self._provider_id == LLMProviderId.OPENAI_CODEX:
            return ModelAPISurface(kind="responses")
        if self._provider_id not in {LLMProviderId.OPENAI, LLMProviderId.GITHUB_COPILOT}:
            return ModelAPISurface(kind="chat_completions")
        if normalized.startswith("gpt-5") or normalized.startswith(("o1", "o3", "o4")):
            return ModelAPISurface(kind="responses")
        return ModelAPISurface(kind="chat_completions")

    def _fallback_http_status(self, request: LLMRequest, exc: httpx.HTTPStatusError) -> LLMResponse:
        status_code = exc.response.status_code
        provider_detail_raw = self._extract_provider_error_detail(exc.response, truncate=False)
        provider_detail = (
            self._truncate_provider_detail(provider_detail_raw) if provider_detail_raw else None
        )
        if self._is_context_window_error(status_code=status_code, provider_detail=provider_detail_raw):
            detail_suffix = f" Provider detail: {provider_detail}" if provider_detail else ""
            message_text = (
                "LLM request was rejected because the input exceeded the model context window."
                f"{detail_suffix} The runtime may need to compact older context before retrying."
            )
            return self._fallback_response(
                request,
                error_code="llm_context_window_exceeded",
                error_detail=provider_detail,
                message=message_text,
            )
        if self._provider_id not in {
            LLMProviderId.OPENAI,
            LLMProviderId.OPENAI_CODEX,
            LLMProviderId.GITHUB_COPILOT,
        }:
            return self._fallback_response(
                request,
                error_code=f"llm_provider_http_{status_code}",
                error_detail=provider_detail,
            )
        if status_code in {401, 403}:
            return self._fallback_response(
                request,
                error_code="llm_provider_auth_error",
                message="LLM provider rejected the configured credentials. Check provider auth settings.",
            )
        if status_code == 404:
            if self._is_codex_stateless_item_replay_error(provider_detail=provider_detail_raw):
                detail_suffix = f" Provider detail: {provider_detail}" if provider_detail else ""
                message_text = (
                    "LLM request was rejected by the provider."
                    f"{detail_suffix} Check the configured model, API surface, and tool payload."
                )
                return self._fallback_response(
                    request,
                    error_code="llm_provider_invalid_request",
                    error_detail=provider_detail,
                    message=message_text,
                )
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
            detail_suffix = f" Provider detail: {provider_detail}" if provider_detail else ""
            message_text = (
                "LLM request was rejected by the provider."
                f"{detail_suffix} Check the configured model, API surface, and tool payload."
            )
            return self._fallback_response(
                request,
                error_code="llm_provider_invalid_request",
                error_detail=provider_detail,
                message=message_text,
            )
        return self._fallback_response(request, error_code="llm_provider_unavailable")

    @staticmethod
    def _is_context_window_error(*, status_code: int, provider_detail: str | None) -> bool:
        """Return whether provider rejection indicates a context window overflow."""

        if status_code == 413:
            return True
        if not provider_detail:
            return False
        normalized = " ".join(provider_detail.lower().split())
        markers = (
            "context window",
            "maximum context length",
            "context_length_exceeded",
            "too many tokens",
            "input exceeds the context window",
            "input exceeded the context window",
            "input is too long",
            "request is too large for the model",
        )
        return any(marker in normalized for marker in markers)

    def _is_codex_stateless_item_replay_error(self, *, provider_detail: str | None) -> bool:
        """Return whether Codex returned stateless replay lookup failure with `store=false`."""

        if self._provider_id != LLMProviderId.OPENAI_CODEX or not provider_detail:
            return False
        normalized = " ".join(provider_detail.lower().split())
        return (
            "item with id" in normalized
            and "not found" in normalized
            and "store" in normalized
            and "false" in normalized
        )

    @staticmethod
    def _extract_provider_error_detail(response: httpx.Response, *, truncate: bool = True) -> str | None:
        """Return a short provider-supplied 4xx detail when one is available."""

        def _normalize(value: str) -> str:
            if truncate:
                return OpenAICompatibleChatProvider._truncate_provider_detail(value)
            return " ".join(value.split()).strip()

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return _normalize(message)
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return _normalize(message)
        raw_text = response.text.strip()
        if not raw_text:
            return None
        compact = " ".join(raw_text.split())
        return _normalize(compact)

    @staticmethod
    def _truncate_provider_detail(value: str, *, max_chars: int = 300) -> str:
        """Bound provider detail length before surfacing in user-visible fallback text."""

        compact = " ".join(value.split()).strip()
        if not compact:
            return ""
        if len(compact) <= max_chars:
            return compact
        return f"{compact[:max_chars].rstrip()}..."


def build_llm_provider(
    settings: Settings,
    *,
    runtime_secrets_update_hook: Callable[[dict[str, str]], None] | None = None,
) -> LLMProvider:
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
        minimax_portal_refresh_token=settings.minimax_portal_refresh_token,
        minimax_portal_token_expires_at=settings.minimax_portal_token_expires_at,
        minimax_portal_resource_url=settings.minimax_portal_resource_url,
        minimax_portal_region=settings.minimax_portal_region,
        runtime_secrets_update_hook=runtime_secrets_update_hook,
    )


def append_user_message(history: list[LLMMessage], message: str) -> None:
    """Append one user message to provider history."""

    history.append(LLMMessage(role="user", content=message))
