"""Tests for browser carryover prompt compaction."""

from __future__ import annotations

from afkbot.services.agent_loop.browser_carryover import BrowserCarryoverService
from afkbot.settings import Settings


def test_browser_carryover_truncates_on_bullet_boundaries(tmp_path) -> None:
    """Carryover truncation should avoid cutting one bullet line mid-sentence."""

    service = BrowserCarryoverService(
        settings=Settings(root_dir=tmp_path),
        runlog_repo=object(),  # type: ignore[arg-type]
        max_chars=200,
    )

    summary = "\n".join(
        [
            "Trusted live browser carryover from the current runtime.",
            "- Browser session status: open in the current runtime and should be reused instead of reopening the site unless recovery is required.",
            "- Live page URL: https://example.com/dashboard?tab=orders&view=expanded&filter=high-priority",
        ]
    )

    clipped = service._truncate_summary(summary)

    assert clipped.endswith("\n- Carryover text truncated.")
    assert "- Browser session status: open in the current runtime." not in clipped
    assert "- Live page URL: https://example.com/dashboard" not in clipped
