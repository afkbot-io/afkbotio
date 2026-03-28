"""Tool plugin for http.request."""
# mypy: disable-error-code="attr-defined,override,arg-type"

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request

from pydantic import Field

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.network import (
    build_pinned_opener,
    resolve_host_addresses as _resolve_host_addresses_shared,
)
from afkbot.services.tools.network import (
    resolve_public_network_addresses as _resolve_public_network_addresses_shared,
)
from afkbot.services.tools.credential_placeholders import (
    redact_secret_fragments,
    redact_secret_values_in_payload,
    resolve_secret_placeholders,
)
from afkbot.services.tools.params import AppToolParameters, ToolParameters
from afkbot.settings import Settings


class HttpRequestParams(AppToolParameters):
    """Parameters for http.request tool."""

    method: str = Field(default="GET", min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=200_000)
    auth_credential_name: str | None = Field(default=None, min_length=1, max_length=128)
    auth_header_name: str = Field(default="Authorization", min_length=1, max_length=128)


class HttpRequestTool(ToolBase):
    """Execute one outbound HTTP request with deterministic response payload."""

    name = "http.request"
    description = "Run outbound HTTP request and return status/headers/body."
    parameters_model = HttpRequestParams
    required_skill = "http-request"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=HttpRequestParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        resolved_values: set[str] = set()
        try:
            headers = dict(payload.headers)
            if payload.auth_credential_name is not None:
                resolved_header_name = payload.auth_header_name.strip()
                if not resolved_header_name:
                    return ToolResult.error(
                        error_code="http_request_invalid",
                        reason="auth_header_name cannot be empty when auth_credential_name is used",
                    )
                if resolved_header_name not in headers:
                    resolved_auth_value = await self._resolve_credential(
                        profile_id=ctx.profile_id,
                        credential_profile_key=payload.credential_profile_key,
                        credential_name=payload.auth_credential_name,
                    )
                    headers[resolved_header_name] = resolved_auth_value
                    resolved_values.add(resolved_auth_value)
            resolved_headers = {
                str(key): await resolve_secret_placeholders(
                    settings=self._settings,
                    profile_id=ctx.profile_id,
                    source=str(value),
                    default_app_name="http",
                    default_profile_name=payload.credential_profile_key,
                    tool_name=self.name,
                    allowed_app_names={"http"},
                    resolved_values=resolved_values,
                )
                for key, value in headers.items()
            }
            resolved_url = await resolve_secret_placeholders(
                settings=self._settings,
                profile_id=ctx.profile_id,
                source=payload.url,
                default_app_name="http",
                default_profile_name=payload.credential_profile_key,
                tool_name=self.name,
                allowed_app_names={"http"},
                resolved_values=resolved_values,
            )
            resolved_body = (
                None
                if payload.body is None
                else await resolve_secret_placeholders(
                    settings=self._settings,
                    profile_id=ctx.profile_id,
                    source=payload.body,
                    default_app_name="http",
                    default_profile_name=payload.credential_profile_key,
                    tool_name=self.name,
                    allowed_app_names={"http"},
                    resolved_values=resolved_values,
                )
            )
            resolved_payload = HttpRequestParams.model_validate(
                {
                    **payload.model_dump(),
                    "url": resolved_url,
                    "headers": resolved_headers,
                    "body": resolved_body,
                }
            )
            response = await self._perform_request(payload=resolved_payload, headers=resolved_headers)
            if resolved_values:
                response["body"] = redact_secret_fragments(
                    source=str(response.get("body") or ""),
                    secret_values=resolved_values,
                )
                response["headers"] = redact_secret_values_in_payload(
                    value=response.get("headers"),
                    secret_values=resolved_values,
                )
                response["json"] = redact_secret_values_in_payload(
                    value=response.get("json"),
                    secret_values=resolved_values,
                )
            response["url"] = payload.url
            return ToolResult(ok=True, payload=response)
        except CredentialsServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata={
                    "integration_name": "http",
                    "tool_name": self.name,
                    "credential_profile_key": payload.credential_profile_key,
                    **exc.details,
                },
            )
        except ValueError as exc:
            return ToolResult.error(
                error_code="http_request_invalid",
                reason=self._sanitize_error_reason(reason=str(exc), resolved_values=resolved_values),
            )
        except TimeoutError:
            return ToolResult.error(
                error_code="http_request_failed",
                reason=self._sanitize_error_reason(
                    reason=f"Request timed out after {payload.timeout_sec} seconds",
                    resolved_values=resolved_values,
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="http_request_failed",
                reason=self._sanitize_error_reason(
                    reason=f"{exc.__class__.__name__}: {exc}",
                    resolved_values=resolved_values,
                ),
            )

    async def _perform_request(
        self,
        *,
        payload: HttpRequestParams,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        method = payload.method.strip().upper()
        if not method:
            raise ValueError("method is required")
        parsed = urlparse(payload.url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Only http/https URLs are allowed")
        resolved_addresses = self._resolve_public_network_addresses(parsed)

        return await asyncio.wait_for(
            asyncio.to_thread(
                self._perform_request_sync,
                method=method,
                url=payload.url,
                headers=headers,
                body=payload.body,
                timeout_sec=payload.timeout_sec,
                max_body_bytes=self._settings.runtime_max_body_bytes,
                resolved_addresses=resolved_addresses,
            ),
            timeout=float(payload.timeout_sec),
        )

    async def _resolve_credential(
        self,
        *,
        profile_id: str,
        credential_profile_key: str | None,
        credential_name: str,
    ) -> str:
        service = get_credentials_service(self._settings)
        return await service.resolve_plaintext_for_app_tool(
            profile_id=profile_id,
            tool_name="app.run",
            integration_name="http",
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
        )

    @staticmethod
    def _perform_request_sync(
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: str | None,
        timeout_sec: int,
        max_body_bytes: int,
        resolved_addresses: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        encoded_body = body.encode("utf-8") if body is not None else None
        request = Request(
            url=url,
            data=encoded_body,
            method=method,
            headers={str(key): str(value) for key, value in headers.items()},
        )
        try:
            effective_addresses = (
                resolved_addresses
                if resolved_addresses is not None
                else HttpRequestTool._resolve_public_network_addresses(urlparse(url))
            )
            opener = build_pinned_opener(
                url=url,
                resolved_addresses=effective_addresses,
            )
            with opener.open(request, timeout=float(timeout_sec)) as response:
                raw_body, body_truncated = HttpRequestTool._read_limited(
                    response=response,
                    max_body_bytes=max_body_bytes,
                )
                text_body = raw_body.decode("utf-8", errors="replace")
                return {
                    "method": method,
                    "url": url,
                    "status_code": int(getattr(response, "status", 0) or 0),
                    "headers": dict(response.headers.items()),
                    "body": text_body,
                    "body_truncated": body_truncated,
                    "json": HttpRequestTool._try_parse_json(text_body),
                }
        except HTTPError as exc:
            response_raw, response_truncated = HttpRequestTool._read_limited(
                response=exc,
                max_body_bytes=max_body_bytes,
            )
            response_body = response_raw.decode("utf-8", errors="replace")
            suffix = " [truncated]" if response_truncated else ""
            raise RuntimeError(
                f"HTTP {exc.code}: {response_body}{suffix}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    @staticmethod
    def _sanitize_error_reason(*, reason: str, resolved_values: set[str]) -> str:
        if not resolved_values:
            return reason
        return redact_secret_fragments(source=reason, secret_values=resolved_values)

    @staticmethod
    def _read_limited(*, response: object, max_body_bytes: int) -> tuple[bytes, bool]:
        if max_body_bytes < 1:
            max_body_bytes = 1
        stream = getattr(response, "read", None)
        if stream is None:
            return b"", False
        chunk = stream(max_body_bytes + 1)
        if not isinstance(chunk, (bytes, bytearray)):
            return b"", False
        raw = bytes(chunk)
        if len(raw) <= max_body_bytes:
            return raw, False
        return raw[:max_body_bytes], True

    @staticmethod
    def _try_parse_json(value: str) -> object | None:
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return HttpRequestTool._to_jsonable(parsed)

    @staticmethod
    def _to_jsonable(value: object) -> object:
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, list):
            return [HttpRequestTool._to_jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [HttpRequestTool._to_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): HttpRequestTool._to_jsonable(item) for key, item in value.items()}
        return repr(value)

    @staticmethod
    def _resolve_public_network_addresses(parsed_url: object) -> tuple[str, ...]:
        return _resolve_public_network_addresses_shared(
            parsed_url,
            resolver=lambda host, port: HttpRequestTool._resolve_host_addresses(host=host, port=port),
        )

    @staticmethod
    def _resolve_host_addresses(*, host: str, port: int) -> tuple[str, ...]:
        return _resolve_host_addresses_shared(host, port)

def create_tool(settings: Settings) -> ToolBase:
    """Create http.request tool instance."""

    return HttpRequestTool(settings=settings)
