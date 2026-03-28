"""Tests for chat prompt runtime setup and refresh helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from afkbot.cli.presentation.chat_workspace.runtime import (
    build_chat_workspace_catalog_store,
    build_chat_workspace_catalog_refresher,
)
from afkbot.services.chat_session.input_catalog import ChatInputCatalog, ChatInputCatalogStore
from afkbot.settings import get_settings
from tests.cli.test_chat_stub import _prepare_env


def test_build_chat_workspace_catalog_store_uses_latest_catalog_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Catalog-store setup should collect the latest workspace completion snapshot."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)

    async def _fake_build_chat_input_catalog(**_: object) -> object:
        return ChatInputCatalog(
            skill_names=("security-secrets",),
            subagent_names=("reviewer",),
            file_paths=("bootstrap/AGENTS.md",),
        )

    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.runtime.build_chat_input_catalog",
        _fake_build_chat_input_catalog,
    )

    # Act
    with asyncio.Runner() as runner:
        catalog_store = build_chat_workspace_catalog_store(
            runner=runner,
            settings=get_settings(),
            profile_id="default",
        )

    # Assert
    assert catalog_store is not None
    seen_catalog = catalog_store.current()
    assert getattr(seen_catalog, "skill_names") == ("security-secrets",)
    assert getattr(seen_catalog, "subagent_names") == ("reviewer",)
    assert getattr(seen_catalog, "file_paths") == ("bootstrap/AGENTS.md",)


async def test_repl_catalog_refresher_updates_store_with_latest_snapshot() -> None:
    """The catalog refresh closure should swap in newly collected completion data."""

    # Arrange
    store = ChatInputCatalogStore(
        ChatInputCatalog(
            skill_names=("security-secrets",),
            subagent_names=(),
            file_paths=(),
        )
    )

    async def _fake_build_chat_input_catalog(**_: object) -> ChatInputCatalog:
        return ChatInputCatalog(
            skill_names=("security-secrets",),
            subagent_names=("reviewer",),
            file_paths=("bootstrap/AGENTS.md",),
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.runtime.build_chat_input_catalog",
        _fake_build_chat_input_catalog,
    )

    # Act
    try:
        refresh = build_chat_workspace_catalog_refresher(
            settings=object(),  # type: ignore[arg-type]
            profile_id="default",
            catalog_store=store,
        )
        await refresh()
    finally:
        monkeypatch.undo()

    # Assert
    assert store.current().subagent_names == ("reviewer",)
