"""Tool plugin for web.search via Brave Search API."""

from __future__ import annotations

from collections.abc import Mapping
import json

import httpx
from pydantic import Field, field_validator

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_FRESHNESS_VALUES = frozenset({"pd", "pw", "pm", "py"})


class WebSearchParams(RoutedToolParameters):
    """Parameters for web.search."""

    query: str = Field(min_length=1, max_length=512)
    count: int = Field(default=5, ge=1, le=20)
    lang: str | None = Field(default=None, min_length=2, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=16)
    freshness: str | None = Field(default=None, min_length=2, max_length=8)

    @field_validator("freshness", mode="before")
    @classmethod
    def _validate_freshness(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if normalized not in _FRESHNESS_VALUES:
            raise ValueError("freshness must be one of: pd, pw, pm, py")
        return normalized

    @field_validator("lang", "country", mode="before")
    @classmethod
    def _normalize_locale_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None


class WebSearchTool(ToolBase):
    """Run web search through Brave Search API."""

    name = "web.search"
    description = "Search web via Brave Search API and return normalized results."
    parameters_model = WebSearchParams
    required_skill = "web-search"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=WebSearchParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        api_key = str(self._settings.brave_api_key or "").strip()
        if not api_key:
            return ToolResult.error(
                error_code="web_search_api_key_missing",
                reason="Brave Search API key is missing (AFKBOT_BRAVE_API_KEY)",
            )

        try:
            response_payload = await self._perform_search(payload=payload, api_key=api_key)
            return ToolResult(ok=True, payload=response_payload)
        except ValueError as exc:
            return ToolResult.error(error_code="web_search_invalid", reason=str(exc))
        except TimeoutError:
            return ToolResult.error(
                error_code="web_search_failed",
                reason=f"Search request timed out after {payload.timeout_sec} seconds",
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            return ToolResult.error(
                error_code="web_search_failed",
                reason=f"Search API returned HTTP {status_code}",
            )
        except httpx.RequestError as exc:
            return ToolResult.error(
                error_code="web_search_failed",
                reason=f"Search request failed: {exc.__class__.__name__}",
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="web_search_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )

    async def _perform_search(
        self,
        *,
        payload: WebSearchParams,
        api_key: str,
    ) -> dict[str, object]:
        request_params: dict[str, str | int] = {
            "q": payload.query,
            "count": payload.count,
        }
        if payload.lang is not None:
            request_params["search_lang"] = payload.lang
        if payload.country is not None:
            request_params["country"] = payload.country
        if payload.freshness is not None:
            request_params["freshness"] = payload.freshness

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }
        timeout = httpx.Timeout(timeout=float(payload.timeout_sec))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _BRAVE_SEARCH_URL,
                params=request_params,
                headers=headers,
            )
            response.raise_for_status()

        data = self._decode_json_body(response.text)
        web_section = data.get("web")
        if not isinstance(web_section, dict):
            web_section = {}
        raw_results = web_section.get("results")
        if not isinstance(raw_results, list):
            raw_results = []

        results: list[dict[str, object]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_result(item)
            if normalized is None:
                continue
            results.append(normalized)

        payload_result: dict[str, object] = {
            "query": payload.query,
            "count": len(results),
            "results": results,
        }
        meta: dict[str, object] = {}
        if payload.lang is not None:
            meta["lang"] = payload.lang
        if payload.country is not None:
            meta["country"] = payload.country
        if payload.freshness is not None:
            meta["freshness"] = payload.freshness
        if meta:
            payload_result["meta"] = meta
        return payload_result

    @staticmethod
    def _decode_json_body(raw_text: str) -> dict[str, object]:
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError("Search API returned invalid JSON payload") from exc
        if not isinstance(data, dict):
            raise ValueError("Search API returned unexpected payload")
        return {str(key): value for key, value in data.items()}

    @staticmethod
    def _normalize_result(item: Mapping[str, object]) -> dict[str, object] | None:
        url = str(item.get("url") or "").strip()
        if not url:
            return None
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or item.get("snippet") or "").strip()
        age = str(item.get("age") or "").strip()
        result: dict[str, object] = {
            "url": url,
            "title": title,
            "description": description,
        }
        if age:
            result["age"] = age
        return result


def create_tool(settings: Settings) -> ToolBase:
    """Create web.search tool instance."""

    return WebSearchTool(settings=settings)
