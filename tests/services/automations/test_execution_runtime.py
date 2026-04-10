"""Unit tests for automation execution runtime helpers."""

from __future__ import annotations

import pytest

from afkbot.services.automations.session_runner_factory import build_automation_session_runner
from afkbot.settings import Settings


class _DummyLoop:
    """Marker loop object for factory compatibility tests."""


def test_build_session_runner_uses_profile_aware_factory() -> None:
    """Automation runner factory must receive explicit profile context."""

    expected = _DummyLoop()

    def factory(session_factory: object, profile_id: str) -> _DummyLoop:
        assert session_factory is session_factory_obj
        assert profile_id == "default"
        return expected

    session_factory_obj = object()
    assert build_automation_session_runner(
        runner_factory=factory,
        session_factory=session_factory_obj,
        profile_id="default",
        settings=Settings(),
    ) is expected


def test_build_session_runner_rejects_unsupported_factory_signature() -> None:
    """Factories without profile_id parameter should fail loudly."""

    def factory() -> _DummyLoop:
        return _DummyLoop()

    with pytest.raises(TypeError):
        build_automation_session_runner(
            runner_factory=factory,
            session_factory=object(),
            profile_id="default",
            settings=Settings(),
        )
