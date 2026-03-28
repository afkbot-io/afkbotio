"""Content fetching/parsing helpers for the skill marketplace."""

from __future__ import annotations

import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from afkbot.services.skills.markdown import extract_summary
from afkbot.services.skills.marketplace_contracts import SkillMarketplaceError


class MarketplaceContentFetcher:
    """Fetch remote marketplace payloads with consistent limits and errors."""

    def __init__(
        self,
        *,
        fetch_text: Callable[[str, int], str] | None,
        max_markdown_bytes: int,
        max_json_bytes: int,
        http_timeout_sec: int,
        http_user_agent: str,
    ) -> None:
        self._fetch_text = fetch_text or self._default_fetch_text
        self._max_markdown_bytes = max_markdown_bytes
        self._max_json_bytes = max_json_bytes
        self._http_timeout_sec = http_timeout_sec
        self._http_user_agent = http_user_agent

    def fetch_markdown(self, url: str) -> str:
        """Fetch one remote SKILL.md payload."""

        payload = self._fetch_text(url, self._max_markdown_bytes)
        if not payload.strip():
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source SKILL.md is empty",
            )
        return payload

    def fetch_html(self, url: str) -> str:
        """Fetch one remote HTML payload."""

        payload = self._fetch_text(url, self._max_json_bytes)
        if not payload.strip():
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source HTML payload is empty",
            )
        return payload

    def fetch_json(self, url: str) -> dict[str, object]:
        """Fetch and decode one JSON payload."""

        payload = self._fetch_text(url, self._max_json_bytes)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source returned invalid JSON",
            ) from exc
        if not isinstance(parsed, dict):
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source returned unexpected JSON payload",
            )
        return parsed

    def _default_fetch_text(self, url: str, max_bytes: int) -> str:
        request = Request(
            url=url,
            headers={
                "User-Agent": self._http_user_agent,
                "Accept": "application/json, text/plain, */*",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._http_timeout_sec) as response:  # noqa: S310
                raw = response.read(max_bytes + 1)
                if not isinstance(raw, (bytes, bytearray)):
                    raise SkillMarketplaceError(
                        error_code="skill_marketplace_fetch_failed",
                        reason="Source payload type is invalid",
                    )
                raw_bytes = bytes(raw)
                if len(raw_bytes) > max_bytes:
                    raise SkillMarketplaceError(
                        error_code="skill_marketplace_source_too_large",
                        reason=f"Source payload exceeds {max_bytes} bytes",
                    )
                encoding = str(response.headers.get_content_charset() or "utf-8")
                return raw_bytes.decode(encoding, errors="replace")
        except HTTPError as exc:
            if exc.code == 404:
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_source_not_found",
                    reason="Source not found",
                ) from exc
            raise SkillMarketplaceError(
                error_code="skill_marketplace_fetch_failed",
                reason=f"HTTP {exc.code} while fetching source",
            ) from exc
        except URLError as exc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_fetch_failed",
                reason=f"Failed to fetch source: {exc.reason}",
            ) from exc
        except TimeoutError as exc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_fetch_failed",
                reason="Fetching source timed out",
            ) from exc
        except OSError as exc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_fetch_failed",
                reason=f"Failed to fetch source: {exc.__class__.__name__}",
            ) from exc


def extract_heading(content: str) -> str | None:
    """Return first markdown heading from SKILL.md text, if present."""

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                return heading
    return None


def extract_marketplace_summary(content: str) -> str:
    """Return compact summary used in marketplace install results."""

    return extract_summary(content)
