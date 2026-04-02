"""Payload and response helpers for OpenAI-compatible providers."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from afkbot.services.llm.contracts import (
    LLMRequest,
    LLMResponse,
    LLMToolDefinition,
    ToolCallRequest,
)


class OpenAICompatiblePayloadRuntime:
    """Reusable payload/rendering helpers for OpenAI-compatible providers."""

    def _build_messages(
        self,
        request: LLMRequest,
        *,
        encode_tool_name: Callable[[str], str] | None = None,
        assistant_tool_call_content_mode: Literal["omit", "null"] = "omit",
    ) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = [{"role": "system", "content": request.context}]
        for item in request.history:
            message: dict[str, object] = {"role": item.role}
            if item.role == "assistant" and item.tool_calls:
                message["tool_calls"] = self._render_assistant_tool_calls(
                    item.tool_calls,
                    encode_tool_name=encode_tool_name,
                )
                if item.content:
                    message["content"] = item.content
                elif assistant_tool_call_content_mode == "null":
                    # OpenAI Chat Completions expects explicit null content when replaying
                    # assistant tool-calls into the next request.
                    message["content"] = None
            elif item.role == "tool":
                if item.tool_call_id:
                    message["tool_call_id"] = item.tool_call_id
                message["content"] = item.content or ""
            elif item.content is not None:
                message["content"] = item.content
            messages.append(message)
        return messages

    def _build_responses_input(
        self,
        request: LLMRequest,
        *,
        encode_tool_name: Callable[[str], str] | None = None,
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        encode = encode_tool_name or self._identity_tool_name
        for message in request.history:
            if message.role == "assistant" and message.provider_items:
                items.extend(self._clone_provider_items(message.provider_items))
                if message.content:
                    items.append(
                        self._build_responses_message_item(
                            role="assistant",
                            content=message.content,
                        )
                    )
                continue
            if message.role == "tool":
                if message.tool_call_id:
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": message.tool_call_id,
                            "output": message.content or "",
                        }
                    )
                    continue
                if message.content is not None:
                    items.append({"role": "tool", "content": message.content})
                continue
            if message.role == "assistant" and message.tool_calls:
                if message.content:
                    items.append(
                        self._build_responses_message_item(
                            role="assistant",
                            content=message.content,
                        )
                    )
                for idx, call in enumerate(message.tool_calls, start=1):
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": (call.call_id or "").strip() or f"call_{idx}",
                            "name": encode(call.name),
                            "arguments": json.dumps(call.params, ensure_ascii=True, sort_keys=True),
                        }
                    )
                continue
            if message.content is not None:
                items.append(
                    self._build_responses_message_item(
                        role=message.role,
                        content=message.content,
                    )
                )
        return items

    @staticmethod
    def _build_responses_message_item(*, role: str, content: str) -> dict[str, object]:
        """Render one explicit Responses input message item for plain text history."""

        content_type = "output_text" if role == "assistant" else "input_text"
        return {
            "type": "message",
            "role": role,
            "content": [
                {
                    "type": content_type,
                    "text": content,
                }
            ],
        }

    @staticmethod
    def _render_assistant_tool_calls(
        calls: list[ToolCallRequest],
        *,
        encode_tool_name: Callable[[str], str] | None = None,
    ) -> list[dict[str, object]]:
        rendered: list[dict[str, object]] = []
        encode = encode_tool_name or OpenAICompatiblePayloadRuntime._identity_tool_name
        for idx, call in enumerate(calls, start=1):
            call_id = (call.call_id or "").strip() or f"call_{idx}"
            rendered.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": encode(call.name),
                        "arguments": json.dumps(call.params, ensure_ascii=True, sort_keys=True),
                    },
                }
            )
        return rendered

    @staticmethod
    def _build_tools(
        tool_defs: tuple[LLMToolDefinition, ...],
        *,
        encode_tool_name: Callable[[str], str] | None = None,
    ) -> list[dict[str, object]]:
        tools: list[dict[str, object]] = []
        encode = encode_tool_name or OpenAICompatiblePayloadRuntime._identity_tool_name
        for definition in tool_defs:
            description = definition.description.strip()
            if definition.required_skill:
                description = (
                    f"{description} This tool is routed through the '{definition.required_skill}' "
                    "skill. Follow that SKILL.md workflow."
                )
            if definition.requires_confirmation:
                description = (
                    f"{description} This tool may require explicit user approval before execution."
                )
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": encode(definition.name),
                        "description": description,
                        "parameters": definition.parameters_schema
                        or {"type": "object", "properties": {}, "additionalProperties": True},
                    },
                }
            )
        return tools

    @staticmethod
    def _build_responses_tools(
        tool_defs: tuple[LLMToolDefinition, ...],
        *,
        encode_tool_name: Callable[[str], str] | None = None,
    ) -> list[dict[str, object]]:
        tools: list[dict[str, object]] = []
        encode = encode_tool_name or OpenAICompatiblePayloadRuntime._identity_tool_name
        for definition in tool_defs:
            description = definition.description.strip()
            if definition.required_skill:
                description = (
                    f"{description} This tool is routed through the '{definition.required_skill}' "
                    "skill. Follow that SKILL.md workflow."
                )
            if definition.requires_confirmation:
                description = (
                    f"{description} This tool may require explicit user approval before execution."
                )
            tools.append(
                {
                    "type": "function",
                    "name": encode(definition.name),
                    "description": description,
                    "parameters": definition.parameters_schema
                    or {"type": "object", "properties": {}, "additionalProperties": True},
                }
            )
        return tools

    def _parse_response(
        self,
        payload: Mapping[str, Any],
        request: LLMRequest,
        *,
        decode_tool_name: Callable[[str], str] | None = None,
    ) -> LLMResponse:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("Missing choices")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ValueError("Invalid choice")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("Invalid message")

        tool_calls_raw = message.get("tool_calls")
        if isinstance(tool_calls_raw, list) and tool_calls_raw:
            calls: list[ToolCallRequest] = []
            decode = decode_tool_name or self._identity_tool_name
            for tool_call in tool_calls_raw:
                if not isinstance(tool_call, Mapping):
                    continue
                function_obj = tool_call.get("function")
                if not isinstance(function_obj, Mapping):
                    continue
                name = function_obj.get("name")
                if not isinstance(name, str) or not name:
                    continue
                args_raw = function_obj.get("arguments", "{}")
                try:
                    parsed_args = self._parse_tool_arguments(args_raw)
                except (ValueError, TypeError, json.JSONDecodeError):
                    parsed_args = {}
                call_id_raw = tool_call.get("id")
                call_id = call_id_raw.strip() if isinstance(call_id_raw, str) else None
                if not call_id:
                    call_id = f"call_{len(calls) + 1}"
                calls.append(
                    ToolCallRequest(
                        name=decode(name),
                        params=parsed_args,
                        call_id=call_id,
                    )
                )
            if calls:
                return LLMResponse.tool_calls_response(calls)

        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return LLMResponse.final(content.strip())
        return self._fallback_response(request)

    def _parse_responses_response(
        self,
        payload: Mapping[str, Any],
        request: LLMRequest,
        *,
        decode_tool_name: Callable[[str], str] | None = None,
    ) -> LLMResponse:
        output = payload.get("output")
        if not isinstance(output, list) or not output:
            raise ValueError("Missing output")

        provider_items = self._normalize_provider_items(output)
        decode = decode_tool_name or self._identity_tool_name
        calls: list[ToolCallRequest] = []
        for index, item in enumerate(provider_items, start=1):
            item_type = str(item.get("type") or "").strip()
            if item_type != "function_call":
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            try:
                parsed_args = self._parse_tool_arguments(item.get("arguments", "{}"))
            except (ValueError, TypeError, json.JSONDecodeError):
                parsed_args = {}
            call_id_raw = item.get("call_id") or item.get("id")
            call_id = call_id_raw.strip() if isinstance(call_id_raw, str) else None
            if not call_id:
                call_id = f"call_{index}"
            calls.append(
                ToolCallRequest(
                    name=decode(name),
                    params=parsed_args,
                    call_id=call_id,
                )
            )
        if calls:
            return LLMResponse.tool_calls_response(
                calls,
                provider_items=provider_items,
            )

        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return LLMResponse.final(
                output_text.strip(),
                provider_items=provider_items,
            )

        content = self._extract_responses_message_text(provider_items)
        if content:
            return LLMResponse.final(
                content,
                provider_items=provider_items,
            )
        return self._fallback_response(request)

    @classmethod
    def _parse_tool_arguments(cls, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return {str(key): cls._to_jsonable(item) for key, item in value.items()}
        if not isinstance(value, str) or not value.strip():
            return {}
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            return {}
        return {str(key): cls._to_jsonable(item) for key, item in parsed.items()}

    @classmethod
    def _to_jsonable(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, list):
            return [cls._to_jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._to_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._to_jsonable(item) for key, item in value.items()}
        return repr(value)

    @classmethod
    def _normalize_provider_items(cls, value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            normalized_item = cls._to_jsonable(dict(item))
            if isinstance(normalized_item, dict):
                normalized.append(normalized_item)
        return normalized

    @classmethod
    def _clone_provider_items(cls, items: list[dict[str, object]]) -> list[dict[str, object]]:
        return cls._normalize_provider_items(items)

    @classmethod
    def _extract_responses_message_text(cls, items: list[dict[str, object]]) -> str:
        chunks: list[str] = []
        for item in items:
            if str(item.get("type") or "").strip() != "message":
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                block_type = str(block.get("type") or "").strip()
                if block_type not in {"output_text", "text"}:
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
                    continue
                if isinstance(text, Mapping):
                    text_value = text.get("value")
                    if isinstance(text_value, str) and text_value.strip():
                        chunks.append(text_value.strip())
        return "\n".join(chunk for chunk in chunks if chunk)

    @staticmethod
    def _fallback_response(
        request: LLMRequest,
        *,
        error_code: str = "llm_provider_unavailable",
        message: str = "LLM provider is temporarily unavailable. Please try again shortly.",
    ) -> LLMResponse:
        _ = request
        return LLMResponse.final(
            message,
            error_code=error_code,
        )

    @staticmethod
    def _identity_tool_name(name: str) -> str:
        return name
