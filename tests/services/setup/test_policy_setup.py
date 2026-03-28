"""Tests for setup-time policy orchestration service."""

from __future__ import annotations

import json
from pathlib import Path

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.services.setup.policy_setup import apply_setup_policy
from afkbot.services.policy import PolicySelection, PolicyCapabilityId, PolicyPresetLevel
from afkbot.settings import Settings


async def test_apply_setup_policy_persists_policy_row(tmp_path: Path) -> None:
    """Setup policy orchestration should upsert default profile policy in storage."""

    db_path = tmp_path / "install-policy.db"
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.STRICT,
        capabilities=(PolicyCapabilityId.MEMORY, PolicyCapabilityId.HTTP),
    )

    resolved = await apply_setup_policy(
        settings=settings,
        profile_id="default",
        selection=selection,
        network_allowlist=("search.brave.com",),
    )

    assert resolved.enabled is True
    assert resolved.preset is PolicyPresetLevel.STRICT

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            row = await ProfilePolicyRepository(session).get("default")
            assert row is not None
            assert row.policy_enabled is True
            assert row.policy_preset == "strict"
            assert row.max_iterations_main == resolved.max_iterations_main
            assert row.max_iterations_subagent == resolved.max_iterations_subagent
            assert json.loads(row.allowed_directories_json) == [
                str((tmp_path / "profiles/default").resolve()),
            ]
            assert row.network_allowlist_json == '["search.brave.com"]'
    finally:
        await engine.dispose()


async def test_apply_setup_policy_can_disable_enforcement(tmp_path: Path) -> None:
    """Setup policy orchestration should persist disabled policy mode."""

    db_path = tmp_path / "install-policy-disabled.db"
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    selection = PolicySelection(
        enabled=False,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(PolicyCapabilityId.CREDENTIALS,),
    )

    resolved = await apply_setup_policy(
        settings=settings,
        profile_id="default",
        selection=selection,
        network_allowlist=(),
    )

    assert resolved.enabled is False

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            row = await ProfilePolicyRepository(session).get("default")
            assert row is not None
            assert row.policy_enabled is False
            assert row.policy_preset == "medium"
    finally:
        await engine.dispose()
