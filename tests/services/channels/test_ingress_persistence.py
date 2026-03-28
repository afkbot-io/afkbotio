"""Tests for pending ingress persistence service registry semantics."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.settings import Settings


def test_get_channel_ingress_pending_service_returns_fresh_service_outside_running_loop(
    tmp_path: Path,
) -> None:
    """Sync CLI call-sites should not reuse one async pending-ingress service across loops."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'ingress_pending.db'}",
    )

    first = get_channel_ingress_pending_service(settings)
    second = get_channel_ingress_pending_service(settings)

    assert first is not second
