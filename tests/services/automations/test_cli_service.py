"""Tests for automation CLI payload helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.cli_service import (
    graph_show_payload,
    list_automations_payload,
)
from afkbot.services.automations.graph.contracts import AutomationGraphNodeSpec, AutomationGraphSpec
from afkbot.services.automations.service import (
    get_automations_service,
    reset_automations_services_async,
)
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
async def _reset_automation_services() -> None:
    await reset_automations_services_async()
    yield
    await reset_automations_services_async()


async def test_list_automations_payload_does_not_create_profile_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-only automation CLI paths must not repair or create profile layout directories."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_cli.db'}",
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(session_factory) as session:
            await ProfileRepository(session).create(profile_id="ghost", name="Ghost")
        monkeypatch.setattr(
            "afkbot.services.automations.cli_service.get_settings",
            lambda: settings,
        )

        payload = await list_automations_payload(profile_id="ghost")

        assert json.loads(payload) == {"automations": []}
        assert not (tmp_path / "profiles" / "ghost").exists()
    finally:
        await engine.dispose()


async def test_graph_show_payload_does_not_create_profile_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph read payloads must stay read-only and avoid creating profile layout directories."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_cli_graph.db'}",
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await create_schema(engine)
    try:
        async with session_scope(session_factory) as session:
            await ProfileRepository(session).create(profile_id="ghost", name="Ghost")
        monkeypatch.setattr(
            "afkbot.services.automations.cli_service.get_settings",
            lambda: settings,
        )
        monkeypatch.setattr(
            "afkbot.services.automations.service.get_settings",
            lambda: settings,
        )
        service = get_automations_service(settings)
        created = await service.create_webhook(
            profile_id="ghost",
            name="Graph ghost",
            prompt="observe",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="ghost",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="ghost-graph",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    )
                ],
                edges=[],
            ),
        )

        payload = await graph_show_payload(profile_id="ghost", automation_id=created.id)

        data = json.loads(payload)
        assert data["graph"]["automation_id"] == created.id
        assert not (tmp_path / "profiles" / "ghost").exists()
    finally:
        await engine.dispose()
