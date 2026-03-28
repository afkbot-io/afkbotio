"""Workspace prompt-session runtime helpers for interactive chat."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from afkbot.services.chat_session.input_catalog import ChatInputCatalogStore, build_chat_input_catalog
from afkbot.settings import Settings

CatalogRefreshFn = Callable[[], Coroutine[Any, Any, None]]


def build_chat_workspace_catalog_store(
    *,
    runner: asyncio.Runner,
    settings: Settings,
    profile_id: str,
) -> ChatInputCatalogStore:
    """Build one mutable catalog store for the current chat workspace session."""

    return ChatInputCatalogStore(
        runner.run(
            build_chat_input_catalog(
                settings=settings,
                profile_id=profile_id,
            )
        )
    )


def build_chat_workspace_catalog_refresher(
    *,
    settings: Settings,
    profile_id: str,
    catalog_store: ChatInputCatalogStore | None,
) -> CatalogRefreshFn:
    """Build one async closure that refreshes prompt completion suggestions."""

    async def _refresh() -> None:
        if catalog_store is None:
            return
        catalog_store.replace(
            await build_chat_input_catalog(
                settings=settings,
                profile_id=profile_id,
            )
        )

    return _refresh
