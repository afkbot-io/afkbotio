"""Unit tests for automation execution runtime helpers."""

from __future__ import annotations

import pytest

from afkbot.services.automations.loop_factory import build_automation_agent_loop


class _DummyLoop:
    """Marker loop object for factory compatibility tests."""


def test_build_agent_loop_uses_profile_aware_factory() -> None:
    """Automation loop factory must receive explicit profile context."""

    expected = _DummyLoop()

    def factory(session: object, profile_id: str) -> _DummyLoop:
        assert session is session_obj
        assert profile_id == "default"
        return expected

    session_obj = object()
    assert build_automation_agent_loop(
        agent_loop_factory=factory,
        session=session_obj,
        profile_id="default",
    ) is expected


def test_build_agent_loop_rejects_unsupported_factory_signature() -> None:
    """Factories without profile_id parameter should fail loudly."""

    def factory() -> _DummyLoop:
        return _DummyLoop()

    with pytest.raises(TypeError):
        build_automation_agent_loop(
            agent_loop_factory=factory,
            session=object(),
            profile_id="default",
        )
