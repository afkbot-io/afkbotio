"""Trusted browser-state carryover built from recent runlog tool results."""

from __future__ import annotations

import json
from dataclasses import dataclass
import time

from afkbot.services.browser_sessions import BrowserSessionManager, get_browser_session_manager
from afkbot.repositories.runlog_repo import RunlogEventRead, RunlogRepository
from afkbot.services.browser_snapshot import (
    capture_browser_page_snapshot,
    normalize_snapshot_link_list,
    normalize_snapshot_string_list,
    normalize_snapshot_text,
)
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class _BrowserResultEvent:
    event_id: int
    ok: bool
    action: str
    payload: dict[str, object]
    metadata: dict[str, object]
    reason: str


class BrowserCarryoverService:
    """Build a compact trusted browser summary for the next turn context."""

    def __init__(
        self,
        *,
        settings: Settings,
        runlog_repo: RunlogRepository,
        session_manager: BrowserSessionManager | None = None,
        max_events: int = 24,
        max_chars: int = 1_600,
        live_refresh_window_sec: float = 5.0,
    ) -> None:
        self._settings = settings
        self._runlog_repo = runlog_repo
        self._session_manager = session_manager or get_browser_session_manager()
        self._max_events = max(1, int(max_events))
        self._max_chars = max(200, int(max_chars))
        self._live_refresh_window_sec = max(1.0, float(live_refresh_window_sec))

    async def build_prompt_block(self, *, profile_id: str, session_id: str) -> str | None:
        """Return compact trusted browser carryover for one chat session, if any."""

        live_summary = await self._build_live_prompt_block(
            profile_id=profile_id,
            session_id=session_id,
        )
        rows = await self._runlog_repo.list_session_events(
            session_id=session_id,
            event_type="tool.result",
            limit=self._max_events,
        )
        events = [event for row in rows if (event := self._parse_browser_event(row)) is not None]
        if live_summary is not None:
            if not events or events[0].ok:
                return live_summary
            return "\n".join(
                [
                    live_summary,
                    f"- Most recent browser failure before the current live page: `{normalize_snapshot_text(events[0].metadata.get('browser_error_class')) or 'browser_action_failed'}`.",
                    *((
                        [f"- Failure reason: {normalize_snapshot_text(events[0].reason)}"]
                        if normalize_snapshot_text(events[0].reason)
                        else []
                    )),
                ]
            )
        if not events:
            return None

        latest = events[0]
        latest_page_event = next((event for event in events if self._has_page_facts(event)), None)
        lines = [
            "Trusted browser carryover from recent turns in this chat session.",
            "These facts come from previous browser tool results and may be stale; refresh with new browser actions when current page state matters.",
        ]
        lines.append(f"- Most recent browser action: `{latest.action or 'unknown'}`.")
        lines.append(f"- Browser session status: {self._session_state(latest)}.")

        if latest_page_event is not None:
            page_lines = self._page_fact_lines(latest_page_event)
            lines.extend(page_lines)

        if not latest.ok:
            error_class = normalize_snapshot_text(latest.metadata.get("browser_error_class")) or "browser_action_failed"
            reason = normalize_snapshot_text(latest.reason)
            lines.append(f"- Most recent browser failure: `{error_class}`.")
            if reason:
                lines.append(f"- Failure reason: {reason}")

        summary = "\n".join(lines).strip()
        return self._truncate_summary(summary)

    async def _build_live_prompt_block(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> str | None:
        handle = await self._session_manager.get(
            root_dir=self._settings.root_dir,
            profile_id=profile_id,
            session_id=session_id,
            idle_ttl_sec=self._settings.browser_session_idle_ttl_sec,
            touch=False,
        )
        if handle is None:
            return None
        current_url = normalize_snapshot_text(getattr(handle.page, "url", "") or "")
        if self._can_reuse_live_summary(handle=handle, current_url=current_url):
            return handle.live_carryover_summary
        snapshot = await capture_browser_page_snapshot(
            handle.page,
            max_chars=min(self._max_chars, 800),
        )
        summary = self._build_live_summary(snapshot=snapshot)
        handle.live_carryover_summary = summary
        handle.live_carryover_page_url = (
            normalize_snapshot_text(snapshot.get("url")) or current_url
        )
        handle.live_carryover_updated_monotonic = time.monotonic()
        return summary

    def _can_reuse_live_summary(
        self,
        *,
        handle: object,
        current_url: str,
    ) -> bool:
        summary = getattr(handle, "live_carryover_summary", None)
        if not isinstance(summary, str) or not summary.strip():
            return False
        cached_url = normalize_snapshot_text(getattr(handle, "live_carryover_page_url", ""))
        if cached_url != current_url:
            return False
        updated_monotonic = getattr(handle, "live_carryover_updated_monotonic", 0.0)
        if not isinstance(updated_monotonic, (int, float)):
            return False
        return (time.monotonic() - float(updated_monotonic)) < self._live_refresh_window_sec

    def _build_live_summary(self, *, snapshot: dict[str, object]) -> str:
        lines = [
            "Trusted live browser carryover from the current runtime.",
            "The browser session for this chat is open right now and should be reused instead of reopening the site unless recovery is required.",
        ]
        lines.append("- Browser session status: open in the current runtime.")
        url = normalize_snapshot_text(snapshot.get("url"))
        title = normalize_snapshot_text(snapshot.get("title"))
        if url:
            lines.append(f"- Live page URL: {url}")
        if title:
            lines.append(f"- Live page title: {title}")
        lines.extend(self._snapshot_fact_lines(snapshot))
        summary = "\n".join(lines).strip()
        return self._truncate_summary(summary)

    @staticmethod
    def _parse_browser_event(row: RunlogEventRead) -> _BrowserResultEvent | None:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        tool_name = str(payload.get("name") or payload.get("tool_name") or "").strip()
        if tool_name != "browser.control":
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        result_payload = result.get("payload")
        metadata = result.get("metadata")
        if not isinstance(result_payload, dict):
            result_payload = {}
        if not isinstance(metadata, dict):
            metadata = {}
        action = str(result_payload.get("action") or "").strip()
        return _BrowserResultEvent(
            event_id=row.id,
            ok=bool(result.get("ok")),
            action=action,
            payload={str(key): value for key, value in result_payload.items()},
            metadata={str(key): value for key, value in metadata.items()},
            reason=str(result.get("reason") or "").strip(),
        )

    @classmethod
    def _has_page_facts(cls, event: _BrowserResultEvent) -> bool:
        if not event.ok:
            return False
        payload = event.payload
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict) and snapshot:
            return True
        for key in ("url", "title", "text", "content", "headings", "artifact", "path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, list) and value:
                return True
            if isinstance(value, dict) and value:
                return True
        return False

    @staticmethod
    def _session_state(event: _BrowserResultEvent) -> str:
        if event.ok and event.action == "close":
            return "closed by the last successful browser action"
        if not event.ok:
            session_state = normalize_snapshot_text(event.metadata.get("session_state"))
            if session_state:
                return f"{session_state} after the last browser failure"
        return "possibly still open from the last successful browser action"

    @classmethod
    def _page_fact_lines(cls, event: _BrowserResultEvent) -> list[str]:
        payload = event.payload
        snapshot_raw = payload.get("snapshot")
        snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else {}
        lines: list[str] = []
        url = normalize_snapshot_text(payload.get("url"))
        title = normalize_snapshot_text(payload.get("title"))
        if url:
            lines.append(f"- Last known page URL: {url}")
        if title:
            lines.append(f"- Last known page title: {title}")
        lines.extend(
            cls._structured_fact_lines(
                headings_source=snapshot.get("headings") or payload.get("headings"),
                buttons_source=snapshot.get("buttons"),
                links_source=snapshot.get("links"),
                text_value=snapshot.get("text") or payload.get("text"),
            )
        )

        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            files = artifact.get("files")
            if isinstance(files, dict):
                rendered_files = [
                    f"{name}={normalize_snapshot_text(value)}"
                    for name, value in files.items()
                    if normalize_snapshot_text(value)
                ][:4]
                if rendered_files:
                    lines.append(f"- Saved browser artifacts: {', '.join(rendered_files)}")
        screenshot_path = normalize_snapshot_text(payload.get("path"))
        if screenshot_path:
            lines.append(f"- Saved screenshot path: {screenshot_path}")
        return lines

    @classmethod
    def _snapshot_fact_lines(cls, snapshot: dict[str, object]) -> list[str]:
        return cls._structured_fact_lines(
            headings_source=snapshot.get("headings"),
            buttons_source=snapshot.get("buttons"),
            links_source=snapshot.get("links"),
            text_value=snapshot.get("body_text") or snapshot.get("text"),
        )

    @classmethod
    def _structured_fact_lines(
        cls,
        *,
        headings_source: object,
        buttons_source: object,
        links_source: object,
        text_value: object,
    ) -> list[str]:
        lines: list[str] = []
        headings = normalize_snapshot_string_list(headings_source, limit=5)
        if headings:
            lines.append(f"- Headings: {', '.join(headings)}")
        buttons = normalize_snapshot_string_list(buttons_source, limit=5)
        if buttons:
            lines.append(f"- Buttons: {', '.join(buttons)}")
        links = normalize_snapshot_link_list(links_source, limit=4)
        if links:
            lines.append(
                "- Key links: "
                + "; ".join(
                    f"{item['text']} -> {item['href']}" if item["text"] and item["href"] else item["text"] or item["href"]
                    for item in links
                )
            )
        text_excerpt = normalize_snapshot_text(text_value)
        if text_excerpt:
            lines.append(f"- Visible text excerpt: {cls._clip_text(text_excerpt, limit=320)}")
        return lines

    @staticmethod
    def _clip_text(value: str, *, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    def _truncate_summary(self, summary: str) -> str:
        if len(summary) <= self._max_chars:
            return summary
        clipped = summary[: self._max_chars].rstrip()
        if "\n- " in clipped:
            clipped = clipped.rsplit("\n- ", 1)[0].rstrip()
        return f"{clipped}\n- Carryover text truncated."
