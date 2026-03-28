"""Tool plugin for web.fetch with readable extraction."""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import OpenerDirector, Request
from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.network import (
    build_pinned_opener,
    resolve_host_addresses,
    resolve_public_network_addresses,
)
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    flags=re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAGS_RE = re.compile(
    r"</?(p|div|section|article|main|header|footer|nav|aside|li|ul|ol|br|tr|table|h[1-6])\b[^>]*>",
    flags=re.IGNORECASE,
)
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", flags=re.IGNORECASE | re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", flags=re.IGNORECASE | re.DOTALL)
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECT_HOPS = 3


@dataclass(frozen=True, slots=True)
class _FetchedPage:
    status_code: int
    headers: dict[str, str]
    url: str
    body: bytes
    truncated: bool


class _WebFetchHttpStatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Fetch request returned HTTP {status_code}")
        self.status_code = int(status_code)


class _WebFetchRequestError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WebFetchParams(RoutedToolParameters):
    """Parameters for web.fetch."""

    url: str = Field(min_length=1, max_length=4096)
    format: str = Field(default="markdown", min_length=1, max_length=16)
    max_chars: int = Field(default=10_000, ge=1, le=200_000)
    max_bytes: int = Field(default=200_000, ge=1, le=2_000_000)


class WebFetchTool(ToolBase):
    """Fetch one web page and extract readable text/markdown."""

    name = "web.fetch"
    description = "Fetch one URL and return readable text or markdown content."
    parameters_model = WebFetchParams
    required_skill = "web-search"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=WebFetchParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        output_format = payload.format.strip().lower()
        if output_format not in {"text", "markdown"}:
            return ToolResult.error(
                error_code="web_fetch_invalid",
                reason="format must be either 'text' or 'markdown'",
            )
        if not self._is_supported_url(payload.url):
            return ToolResult.error(
                error_code="web_fetch_invalid",
                reason="Only http/https URLs are allowed",
            )

        try:
            response_payload = await self._fetch(
                payload=payload,
                output_format=output_format,
            )
            return ToolResult(ok=True, payload=response_payload)
        except ValueError as exc:
            return ToolResult.error(error_code="web_fetch_invalid", reason=str(exc))
        except TimeoutError:
            return ToolResult.error(
                error_code="web_fetch_failed",
                reason=f"Fetch request timed out after {payload.timeout_sec} seconds",
            )
        except _WebFetchHttpStatusError as exc:
            return ToolResult.error(
                error_code="web_fetch_failed",
                reason=f"Fetch request returned HTTP {exc.status_code}",
            )
        except _WebFetchRequestError as exc:
            return ToolResult.error(
                error_code="web_fetch_failed",
                reason=f"Fetch request failed: {exc.reason}",
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="web_fetch_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )

    async def _fetch(
        self,
        *,
        payload: WebFetchParams,
        output_format: str,
    ) -> dict[str, object]:
        effective_max_bytes = max(
            1,
            min(int(payload.max_bytes), int(self._settings.runtime_max_body_bytes)),
        )
        headers = {"User-Agent": "afkbot/web-fetch"}
        origin_host = self._extract_host(payload.url)
        if origin_host is None:
            raise ValueError("URL host is required")

        current_url = payload.url
        current_addresses = self._resolve_public_network_addresses(current_url)
        for hop in range(_MAX_REDIRECT_HOPS + 1):
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._open_url_sync,
                    url=current_url,
                    headers=headers,
                    max_bytes=effective_max_bytes,
                    timeout_sec=payload.timeout_sec,
                    resolved_addresses=current_addresses,
                ),
                timeout=float(payload.timeout_sec),
            )
            if int(response.status_code) in _REDIRECT_CODES:
                location = str(response.headers.get("location") or "").strip()
                if not location:
                    raise ValueError("Redirect response is missing Location header")
                if hop >= _MAX_REDIRECT_HOPS:
                    raise ValueError("Too many redirects")
                next_url = urljoin(current_url, location)
                if not self._is_supported_url(next_url):
                    raise ValueError("Only http/https URLs are allowed")
                redirect_host = self._extract_host(next_url)
                if redirect_host is None:
                    raise ValueError("URL host is required")
                if redirect_host != origin_host:
                    raise ValueError("Redirect to a different host is not allowed")
                current_addresses = self._resolve_public_network_addresses(next_url)
                current_url = next_url
                continue

            content_type = str(response.headers.get("content-type") or "").strip()
            body_text = response.body.decode("utf-8", errors="replace")
            readable = self._extract_readable(
                body=body_text,
                content_type=content_type,
                output_format=output_format,
            )
            limited_text, truncated_chars = self._truncate_chars(
                value=readable,
                max_chars=payload.max_chars,
            )
            return {
                "url": response.url,
                "status_code": int(response.status_code),
                "content_type": content_type,
                "format": output_format,
                "content": limited_text,
                "bytes_read": len(response.body),
                "truncated_bytes": response.truncated,
                "truncated_chars": truncated_chars,
            }
        raise ValueError("Too many redirects")

    @staticmethod
    def _read_limited(*, response: object, max_bytes: int) -> tuple[bytes, bool]:
        data = bytearray()
        truncated = False
        stream = getattr(response, "read", None)
        if stream is None:
            return b"", False
        chunk = stream(max_bytes + 1)
        if not isinstance(chunk, (bytes, bytearray)):
            return b"", False
        data.extend(chunk[:max_bytes])
        truncated = len(chunk) > max_bytes
        return bytes(data), truncated

    @staticmethod
    def _truncate_chars(*, value: str, max_chars: int) -> tuple[str, bool]:
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars], True

    @staticmethod
    def _is_supported_url(url: str) -> bool:
        parsed = urlparse(url.strip())
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _extract_host(url: str) -> str | None:
        parsed = urlparse(url.strip())
        host = str(parsed.hostname or "").strip().lower()
        return host or None

    @classmethod
    def _resolve_public_network_addresses(cls, url: str) -> tuple[str, ...]:
        return resolve_public_network_addresses(
            url,
            resolver=lambda host, port: cls._resolve_host_addresses(host=host, port=port),
        )

    @staticmethod
    def _resolve_host_addresses(*, host: str, port: int) -> tuple[str, ...]:
        return resolve_host_addresses(host, port)

    @classmethod
    def _open_url_sync(
        cls,
        *,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        timeout_sec: int,
        resolved_addresses: tuple[str, ...],
    ) -> _FetchedPage:
        request = Request(url=url, method="GET", headers={str(key): str(value) for key, value in headers.items()})
        opener: OpenerDirector = build_pinned_opener(url=url, resolved_addresses=resolved_addresses)
        try:
            with opener.open(request, timeout=float(timeout_sec)) as response:
                raw_bytes, truncated = cls._read_limited(response=response, max_bytes=max_bytes)
                return _FetchedPage(
                    status_code=int(getattr(response, "status", 0) or 0),
                    headers=dict(response.headers.items()),
                    url=str(getattr(response, "url", url)),
                    body=raw_bytes,
                    truncated=truncated,
                )
        except HTTPError as exc:
            if int(exc.code) in _REDIRECT_CODES:
                return _FetchedPage(
                    status_code=int(exc.code),
                    headers=dict(exc.headers.items()) if exc.headers is not None else {},
                    url=str(getattr(exc, "url", url)),
                    body=b"",
                    truncated=False,
                )
            raise _WebFetchHttpStatusError(int(exc.code)) from exc
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, BaseException):
                error_name = reason.__class__.__name__
            else:
                error_name = str(reason or exc.__class__.__name__)
            raise _WebFetchRequestError(error_name) from exc

    def _extract_readable(
        self,
        *,
        body: str,
        content_type: str,
        output_format: str,
    ) -> str:
        if "html" not in content_type.lower():
            return body.strip()
        if output_format == "markdown":
            return self._html_to_markdown(body)
        return self._html_to_text(body)

    def _html_to_text(self, raw_html: str) -> str:
        cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)
        cleaned = _BLOCK_TAGS_RE.sub("\n", cleaned)
        cleaned = _TAG_RE.sub(" ", cleaned)
        cleaned = html.unescape(cleaned)
        return self._normalize_whitespace(cleaned)

    def _html_to_markdown(self, raw_html: str) -> str:
        cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)

        def _heading_replace(match: re.Match[str]) -> str:
            level = int(match.group(1))
            content = self._normalize_whitespace(_TAG_RE.sub(" ", match.group(2)))
            if not content:
                return "\n"
            return f"\n{'#' * level} {content}\n"

        cleaned = _HEADING_RE.sub(_heading_replace, cleaned)

        def _list_replace(match: re.Match[str]) -> str:
            content = self._normalize_whitespace(_TAG_RE.sub(" ", match.group(1)))
            if not content:
                return "\n"
            return f"\n- {content}"

        cleaned = _LIST_ITEM_RE.sub(_list_replace, cleaned)
        cleaned = _BLOCK_TAGS_RE.sub("\n", cleaned)
        cleaned = _TAG_RE.sub(" ", cleaned)
        cleaned = html.unescape(cleaned)
        normalized = self._normalize_whitespace(cleaned)
        return normalized

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        lines = []
        for raw_line in value.splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            lines.append(line)
        return "\n".join(lines)


def create_tool(settings: Settings) -> ToolBase:
    """Create web.fetch tool instance."""

    return WebFetchTool(settings=settings)
