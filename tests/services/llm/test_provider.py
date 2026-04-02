"""Tests for OpenAI-compatible provider request and response behavior."""

from __future__ import annotations

import asyncio
import httpx
from pytest import CaptureFixture

from afkbot.services.llm.contracts import (
    LLMMessage,
    LLMRequest,
    LLMToolDefinition,
    ToolCallRequest,
)
from afkbot.services.llm.provider import OpenAICompatibleChatProvider, build_llm_provider
from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.llm.provider_settings import describe_provider_debug_info
from afkbot.settings import Settings


def _request() -> LLMRequest:
    return LLMRequest(
        profile_id="default",
        session_id="s-1",
        context="ctx",
        history=[LLMMessage(role="user", content="hello")],
        available_tools=(
            LLMToolDefinition(
                name="debug.echo",
                description="Echo debug payload",
                parameters_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            ),
        ),
    )


class _SpyProvider(OpenAICompatibleChatProvider):
    def __init__(
        self,
        *,
        provider_id: LLMProviderId,
        model: str,
        responses_payload: dict[str, object] | None = None,
        chat_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            provider_id=provider_id,
            model=model,
            api_key="token",
            base_url="https://api.example.test/v1",
        )
        self.responses_payload = responses_payload or {"output": []}
        self.chat_payload = chat_payload or {"choices": [{"message": {"content": "ok"}}]}
        self.last_chat_payload: dict[str, object] | None = None
        self.last_responses_payload: dict[str, object] | None = None
        self.last_timeout_sec: float | None = None

    async def _post_chat(self, payload: dict[str, object], *, timeout_sec: float) -> dict[str, object]:  # noqa: SLF001
        self.last_chat_payload = payload
        self.last_timeout_sec = timeout_sec
        return self.chat_payload

    async def _post_responses(  # noqa: SLF001
        self,
        payload: dict[str, object],
        *,
        timeout_sec: float,
    ) -> dict[str, object]:
        self.last_responses_payload = payload
        self.last_timeout_sec = timeout_sec
        return self.responses_payload


def test_parse_response_handles_mixed_valid_and_invalid_tool_arguments() -> None:
    """Invalid arguments in one tool call must not drop whole tool-calls response."""

    provider = OpenAICompatibleChatProvider(
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "debug.echo",
                                "arguments": "{bad-json",
                            }
                        },
                        {
                            "function": {
                                "name": "debug.echo",
                                "arguments": '{"message": "ok"}',
                            }
                        },
                    ]
                }
            }
        ]
    }

    response = provider._parse_response(payload, _request())  # noqa: SLF001

    assert response.kind == "tool_calls"
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].params == {}
    assert response.tool_calls[0].call_id == "call_1"
    assert response.tool_calls[1].params == {"message": "ok"}
    assert response.tool_calls[1].call_id == "call_2"


def test_parse_response_falls_back_when_content_and_tool_calls_missing() -> None:
    """Provider should return deterministic fallback when payload has no actionable output."""

    provider = OpenAICompatibleChatProvider(
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )
    payload = {"choices": [{"message": {"content": ""}}]}

    response = provider._parse_response(payload, _request())  # noqa: SLF001

    assert response.kind == "final"
    assert response.final_message == "LLM provider is temporarily unavailable. Please try again shortly."
    assert response.error_code == "llm_provider_unavailable"


def test_build_messages_includes_assistant_tool_calls_and_tool_call_id() -> None:
    """Non-OpenAI chat payloads should keep assistant/tool linkage without null content."""

    provider = OpenAICompatibleChatProvider(
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )
    request = LLMRequest(
        profile_id="default",
        session_id="s-1",
        context="ctx",
        history=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "hello"},
                        call_id="call_debug_1",
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                tool_name="debug.echo",
                tool_call_id="call_debug_1",
                content='{"ok":true}',
            ),
        ],
    )

    messages = provider._build_messages(request)  # noqa: SLF001

    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"] == [
        {
            "id": "call_debug_1",
            "type": "function",
            "function": {
                "name": "debug.echo",
                "arguments": '{"message": "hello"}',
            },
        }
    ]
    assert "content" not in messages[1]
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_debug_1"
    assert "name" not in messages[2]


def test_openai_chat_payload_replays_assistant_tool_calls_with_null_content() -> None:
    """OpenAI chat-completions payload should send explicit null assistant content for tool-calls."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4o-mini",
    )
    request = LLMRequest(
        profile_id="default",
        session_id="s-1",
        context="ctx",
        history=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "hello"},
                        call_id="call_debug_1",
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                tool_name="debug.echo",
                tool_call_id="call_debug_1",
                content='{"ok":true}',
            ),
        ],
        available_tools=_request().available_tools,
    )

    response = asyncio.run(provider.complete(request))

    assert response.kind == "final"
    assert provider.last_chat_payload is not None
    assistant_message = provider.last_chat_payload["messages"][1]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["content"] is None
    assert assistant_message["tool_calls"] == [
        {
            "id": "call_debug_1",
            "type": "function",
            "function": {
                "name": "debug_echo",
                "arguments": '{"message": "hello"}',
            },
        }
    ]


def test_build_tools_uses_structured_schema_and_required_skill_hint() -> None:
    """Provider should render per-tool schema and a skill-routing hint in description."""

    provider = OpenAICompatibleChatProvider(
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )
    tools = provider._build_tools(  # noqa: SLF001
        (
            LLMToolDefinition(
                name="app.run",
                description="Run one app action through unified runtime.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["app_name", "action"],
                    "additionalProperties": False,
                },
                required_skill="telegram",
            ),
        )
    )

    assert len(tools) == 1
    function = tools[0]["function"]
    assert function["name"] == "app.run"
    assert "routed through the 'telegram' skill" in function["description"]
    assert function["parameters"]["required"] == ["app_name", "action"]


def test_build_llm_provider_prefers_provider_specific_over_global_values() -> None:
    """Provider-specific key/base URL should win over stale global values."""

    settings = Settings(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key="stale-global-key",
        llm_base_url="https://stale-global.example/v1",
        openai_api_key="openai-provider-key",
        openai_base_url="https://api.openai.com/v1",
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._api_key == "openai-provider-key"  # noqa: SLF001
    assert provider._base_url == "https://api.openai.com/v1"  # noqa: SLF001


def test_build_llm_provider_uses_global_fallback_when_provider_specific_absent() -> None:
    """Global key/base URL should be used as fallback for selected provider."""

    settings = Settings(
        llm_provider="qwen",
        llm_model="qwen-plus",
        llm_api_key="global-key",
        llm_base_url="https://global-gateway.example/v1",
        qwen_api_key=None,
        qwen_base_url="",
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._api_key == "global-key"  # noqa: SLF001
    assert provider._base_url == "https://global-gateway.example/v1"  # noqa: SLF001


def test_build_llm_provider_supports_claude_provider_specific_settings() -> None:
    """Claude provider should use Anthropic-specific API key/base URL fields."""

    settings = Settings(
        llm_provider="claude",
        llm_model="claude-sonnet-4-6",
        llm_api_key="stale-global-key",
        llm_base_url="https://stale-global.example/v1",
        claude_api_key="anthropic-key",
        claude_base_url="https://api.anthropic.com/v1",
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._api_key == "anthropic-key"  # noqa: SLF001
    assert provider._base_url == "https://api.anthropic.com/v1"  # noqa: SLF001


def test_build_llm_provider_supports_moonshot_provider_specific_settings() -> None:
    """Moonshot provider should use provider-specific API key/base URL fields."""

    settings = Settings(
        llm_provider="moonshot",
        llm_model="kimi-k2.5",
        llm_api_key="stale-global-key",
        llm_base_url="https://stale-global.example/v1",
        moonshot_api_key="moonshot-key",
        moonshot_base_url="https://api.moonshot.ai/v1",
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._api_key == "moonshot-key"  # noqa: SLF001
    assert provider._base_url == "https://api.moonshot.ai/v1"  # noqa: SLF001


def test_build_llm_provider_uses_runtime_timeout_setting() -> None:
    """Provider transport timeout should be configured from runtime settings."""

    settings = Settings(
        llm_provider="openrouter",
        llm_model="minimax/minimax-m2.5",
        llm_request_timeout_sec=42.0,
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._timeout_sec == 42.0  # noqa: SLF001


def test_openai_provider_uses_per_request_timeout_override_for_transport() -> None:
    """Provider transport timeout should follow the current request timeout when present."""

    # Arrange
    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
    )
    request = _request().model_copy(update={"request_timeout_sec": 120.0})

    # Act
    response = asyncio.run(provider.complete(request))

    # Assert
    assert response.kind == "final"
    assert response.final_message == "ok"
    assert provider.last_timeout_sec == 120.0


def test_openai_provider_caps_oversized_per_request_timeout_override() -> None:
    """Provider transport timeout should never exceed the shared 30-minute cap."""

    # Arrange
    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
    )
    request = _request().model_copy(update={"request_timeout_sec": 9999.0})

    # Act
    response = asyncio.run(provider.complete(request))

    # Assert
    assert response.kind == "final"
    assert response.final_message == "ok"
    assert provider.last_timeout_sec == 1800.0


def test_describe_provider_debug_info_reports_api_key_presence() -> None:
    """Provider debug info should only report whether an API key is configured."""

    # Arrange
    settings = Settings(
        llm_provider="openai",
        llm_api_key="generic-token",
        openai_api_key="provider-token",
    )

    # Act
    info = describe_provider_debug_info(
        settings=settings,
        provider_id=LLMProviderId.OPENAI,
    )

    # Assert
    assert info.api_key_present is True


def test_provider_debug_diagnostics_print_redacted_request_metadata(
    capsys: CaptureFixture[str],
) -> None:
    """Debug diagnostics should print only safe request metadata."""

    # Arrange
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4o-mini",
        api_key="secret-token-value",
        base_url="https://api.openai.com/v1",
        debug_diagnostics_enabled=True,
        debug_info=describe_provider_debug_info(
            settings=Settings(
                llm_provider="openai",
                openai_api_key="secret-token-value",
            ),
            provider_id=LLMProviderId.OPENAI,
        ),
    )

    # Act
    provider._emit_debug_diagnostics(  # noqa: SLF001
        stage="request",
        path="/chat/completions",
        timeout_sec=15.0,
    )

    # Assert
    err = capsys.readouterr().err
    assert "[afkbot.llm.debug]" in err
    assert '"provider": "openai"' in err
    assert '"model": "gpt-4o-mini"' in err
    assert '"path": "/chat/completions"' in err
    assert '"api_key_present": true' in err
    assert '"timeout_sec": 15.0' in err
    assert "secret-token-value" not in err
    assert "https://api.openai.com/v1" not in err


def test_provider_debug_source_ignores_whitespace_only_provider_specific_values() -> None:
    """Provider debug info should follow the same normalization as key resolution."""

    # Arrange
    settings = Settings(
        llm_provider="openai",
        llm_api_key="generic-token",
        openai_api_key="   ",
        llm_base_url="https://fallback.example/v1",
        openai_base_url="   ",
    )

    # Act
    info = describe_provider_debug_info(
        settings=settings,
        provider_id=LLMProviderId.OPENAI,
    )

    # Assert
    assert info.api_key_present is True


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def _http_status_error_with_detail(status_code: int, detail: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status_code, request=request, json={"error": {"message": detail}})
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_openai_http_status_401_maps_to_auth_error() -> None:
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    response = provider._fallback_http_status(_request(), _http_status_error(401))  # noqa: SLF001

    assert response.error_code == "llm_provider_auth_error"
    assert "credentials" in (response.final_message or "").lower()


def test_openai_http_status_404_maps_to_model_not_found() -> None:
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    response = provider._fallback_http_status(_request(), _http_status_error(404))  # noqa: SLF001

    assert response.error_code == "llm_provider_model_not_found"
    assert "model or endpoint" in (response.final_message or "").lower()


def test_openai_http_status_429_maps_to_rate_limit() -> None:
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    response = provider._fallback_http_status(_request(), _http_status_error(429))  # noqa: SLF001

    assert response.error_code == "llm_provider_rate_limited"
    assert "rate-limited" in (response.final_message or "").lower()


def test_openai_http_status_400_maps_to_invalid_request() -> None:
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    response = provider._fallback_http_status(_request(), _http_status_error(400))  # noqa: SLF001

    assert response.error_code == "llm_provider_invalid_request"
    assert "rejected by the provider" in (response.final_message or "").lower()


def test_openai_http_status_400_includes_provider_detail() -> None:
    """OpenAI invalid-request fallback should preserve provider error detail for debugging."""

    # Arrange
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Input item 2 uses an unsupported role."}},
    )
    exc = httpx.HTTPStatusError("bad request", request=request, response=response)

    # Act
    result = provider._fallback_http_status(_request(), exc)  # noqa: SLF001

    # Assert
    assert result.error_code == "llm_provider_invalid_request"
    assert result.error_detail == "Input item 2 uses an unsupported role."
    assert "unsupported role" in (result.final_message or "").lower()


def test_openai_http_status_400_maps_context_window_rejection_to_overflow_error() -> None:
    """Context-window rejections should use the dedicated overflow error code."""

    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5.1",
        api_key="token",
        base_url="https://api.openai.com/v1",
    )

    response = provider._fallback_http_status(  # noqa: SLF001
        _request(),
        _http_status_error_with_detail(
            400,
            "Your input exceeds the context window of this model. Please adjust your input and try again.",
        ),
    )

    assert response.error_code == "llm_context_window_exceeded"
    assert response.error_detail is not None
    assert "context window" in response.error_detail.lower()
    assert "context window" in (response.final_message or "").lower()


def test_non_openai_http_status_413_maps_to_context_window_overflow() -> None:
    """413 provider errors should trigger the same overflow classification."""

    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENROUTER,
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )

    response = provider._fallback_http_status(_request(), _http_status_error(413))  # noqa: SLF001

    assert response.error_code == "llm_context_window_exceeded"


def test_build_llm_provider_uses_socks_proxy_when_configured() -> None:
    """Provider transport should keep socks proxy URL when enabled."""

    settings = Settings(
        llm_provider="openrouter",
        llm_model="minimax/minimax-m2.5",
        llm_proxy_type="socks5",
        llm_proxy_url="socks5://127.0.0.1:1080",
    )

    provider = build_llm_provider(settings)

    assert provider is not None
    assert provider._proxy_url == "socks5://127.0.0.1:1080"  # noqa: SLF001


def test_provider_complete_returns_not_configured_error_code_when_api_key_missing() -> None:
    """Provider complete should expose deterministic error code when config is incomplete."""

    # Arrange
    provider = OpenAICompatibleChatProvider(
        provider_id=LLMProviderId.OPENROUTER,
        model="minimax/minimax-m2.5",
        api_key="",
        base_url="https://openrouter.ai/api/v1",
    )

    # Act
    response = asyncio.run(provider.complete(_request()))

    # Assert
    assert response.kind == "final"
    assert response.error_code == "llm_provider_not_configured"


def test_provider_complete_maps_http_status_errors_to_exact_status_code() -> None:
    """HTTP rejections should not be misreported as generic network failures."""

    class _HTTPStatusProvider(OpenAICompatibleChatProvider):
        async def _post_chat(  # noqa: SLF001
            self,
            payload: dict[str, object],
            *,
            timeout_sec: float,
        ) -> dict[str, object]:
            _ = payload
            _ = timeout_sec
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(400, request=request, json={"error": {"message": "bad request"}})
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    provider = _HTTPStatusProvider(
        model="minimax/minimax-m2.5",
        api_key="token",
        base_url="https://openrouter.ai/api/v1",
    )

    response = asyncio.run(provider.complete(_request()))

    assert response.kind == "final"
    assert response.error_code == "llm_provider_http_400"


def test_openai_gpt4_family_uses_chat_completions_api() -> None:
    """Legacy OpenAI chat models should keep Chat Completions payloads."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
    )

    response = asyncio.run(provider.complete(_request()))

    assert response.kind == "final"
    assert response.final_message == "ok"
    assert provider.last_chat_payload is not None
    assert provider.last_chat_payload["model"] == "gpt-4.1-mini"
    assert "messages" in provider.last_chat_payload
    assert provider.last_responses_payload is None


def test_openai_gpt5_family_uses_responses_api_and_returns_tool_calls() -> None:
    """New OpenAI reasoning families should route through Responses API."""

    # Arrange
    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5",
        responses_payload={
            "output": [
                {"type": "reasoning", "id": "rs_123"},
                {
                    "type": "function_call",
                    "id": "fc_123",
                    "call_id": "call_debug_1",
                    "name": "debug_echo",
                    "arguments": '{"message":"hi"}',
                },
            ]
        },
    )

    # Act
    response = asyncio.run(provider.complete(_request()))

    # Assert
    assert response.kind == "tool_calls"
    assert response.tool_calls[0].name == "debug.echo"
    assert response.tool_calls[0].params == {"message": "hi"}
    assert response.tool_calls[0].call_id == "call_debug_1"
    assert response.provider_items == [
        {"type": "reasoning", "id": "rs_123"},
        {
            "type": "function_call",
            "id": "fc_123",
            "call_id": "call_debug_1",
            "name": "debug_echo",
            "arguments": '{"message":"hi"}',
        },
    ]
    assert provider.last_chat_payload is None
    assert provider.last_responses_payload is not None
    assert provider.last_responses_payload["model"] == "gpt-5"
    assert provider.last_responses_payload["instructions"] == "ctx"
    assert provider.last_responses_payload["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }
    ]
    assert provider.last_responses_payload["tools"] == [
        {
            "type": "function",
            "name": "debug_echo",
            "description": "Echo debug payload",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
        }
    ]


def test_provider_decodes_visible_tool_name_back_to_canonical_name() -> None:
    """Provider should decode provider-safe visible tool names back to canonical names."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
        chat_payload={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_hidden_1",
                                "type": "function",
                                "function": {
                                    "name": "bash_exec",
                                    "arguments": '{"cmd":"ls"}',
                                },
                            }
                        ]
                    }
                }
            ]
        },
    )
    request = _request().model_copy(
        update={
            "available_tools": (
                LLMToolDefinition(
                    name="bash.exec",
                    description="Run bash commands",
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "cmd": {"type": "string"},
                        },
                        "required": ["cmd"],
                        "additionalProperties": False,
                    },
                ),
            ),
        }
    )

    response = asyncio.run(provider.complete(request))

    assert response.kind == "tool_calls"
    assert response.tool_calls == [
        ToolCallRequest(
            name="bash.exec",
            params={"cmd": "ls"},
            call_id="call_hidden_1",
        )
    ]


def test_provider_rejects_unknown_tool_name_from_model() -> None:
    """Provider should fail closed when the model emits a tool outside the visible surface."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
        chat_payload={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_hidden_1",
                                "type": "function",
                                "function": {
                                    "name": "totally_unknown_tool",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    }
                }
            ]
        },
    )

    response = asyncio.run(provider.complete(_request()))

    assert response.kind == "final"
    assert response.error_code == "llm_provider_response_invalid"


def test_provider_encodes_history_tool_calls_with_historical_codec_surface() -> None:
    """Provider should replay prior assistant tool calls even when they are not visible this turn."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-4.1-mini",
    )
    request = _request().model_copy(
        update={
            "history": [
                LLMMessage(
                    role="assistant",
                    tool_calls=[
                        ToolCallRequest(
                            name="bash.exec",
                            params={"cmd": "pwd"},
                            call_id="call_hist_1",
                        )
                    ],
                ),
            ],
        }
    )

    response = asyncio.run(provider.complete(request))

    assert response.kind == "final"
    assert provider.last_chat_payload is not None
    assert provider.last_chat_payload["messages"][1]["tool_calls"] == [
        {
            "id": "call_hist_1",
            "type": "function",
            "function": {
                "name": "bash_exec",
                "arguments": '{"cmd": "pwd"}',
            },
        }
    ]


def test_responses_payload_includes_reasoning_effort_when_requested() -> None:
    """Responses payload should forward structured reasoning effort to OpenAI reasoning models."""

    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5",
        responses_payload={
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "planned"}],
                }
            ]
        },
    )

    response = asyncio.run(
        provider.complete(
            _request().model_copy(
                update={"reasoning_effort": "high"},
            )
        )
    )

    assert response.kind == "final"
    assert response.final_message == "planned"
    assert provider.last_responses_payload is not None
    assert provider.last_responses_payload["reasoning"] == {"effort": "high"}


def test_responses_payload_replays_assistant_history_as_output_text() -> None:
    """Responses payload must encode assistant plain-text history as output text items."""

    # Arrange
    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5",
        responses_payload={
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        },
    )
    request = _request().model_copy(
        update={
            "history": [
                LLMMessage(role="user", content="hello"),
                LLMMessage(role="assistant", content="hi there"),
            ]
        }
    )

    # Act
    response = asyncio.run(provider.complete(request))

    # Assert
    assert response.kind == "final"
    assert response.final_message == "done"
    assert provider.last_responses_payload is not None
    assert provider.last_responses_payload["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi there"}],
        },
    ]


def test_responses_input_replays_prior_response_items_and_tool_outputs() -> None:
    """Responses payload must preserve prior reasoning/tool items before tool output."""

    # Arrange
    provider = _SpyProvider(
        provider_id=LLMProviderId.OPENAI,
        model="gpt-5",
        responses_payload={
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        },
    )
    request = LLMRequest(
        profile_id="default",
        session_id="s-1",
        context="ctx",
        history=[
            LLMMessage(
                role="assistant",
                provider_items=[
                    {"type": "reasoning", "id": "rs_prev"},
                    {
                        "type": "function_call",
                        "id": "fc_prev",
                        "call_id": "call_debug_1",
                        "name": "debug_echo",
                        "arguments": '{"message":"hello"}',
                    },
                ],
                tool_calls=[
                    ToolCallRequest(
                        name="debug.echo",
                        params={"message": "hello"},
                        call_id="call_debug_1",
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                tool_name="debug.echo",
                tool_call_id="call_debug_1",
                content='{"ok":true}',
            ),
        ],
        available_tools=_request().available_tools,
    )

    # Act
    response = asyncio.run(provider.complete(request))

    # Assert
    assert response.kind == "final"
    assert response.final_message == "done"
    assert provider.last_responses_payload is not None
    assert provider.last_responses_payload["input"] == [
        {"type": "reasoning", "id": "rs_prev"},
        {
            "type": "function_call",
            "id": "fc_prev",
            "call_id": "call_debug_1",
            "name": "debug_echo",
            "arguments": '{"message":"hello"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_debug_1",
            "output": '{"ok":true}',
        },
    ]
