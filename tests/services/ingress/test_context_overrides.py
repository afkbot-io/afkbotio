"""Ingress context override helper tests."""

from __future__ import annotations

from afkbot.services.ingress.context_overrides import (
    build_ingress_context_overrides,
    build_ingress_runtime_metadata,
)
from afkbot.services.ingress.policy_overlay import (
    build_tool_profile_policy_overlay_metadata,
    resolve_tool_profile_policy_overlay_value,
)


def test_build_ingress_runtime_metadata_normalizes_selectors() -> None:
    """Ingress metadata should normalize selector values to stable lowercase keys."""

    metadata = build_ingress_runtime_metadata(
        transport=" Automation ",
        account_id=" 42 ",
        user_id=" USER-1 ",
    )
    assert metadata == {
        "transport": "automation",
        "account_id": "42",
        "user_id": "user-1",
    }


def test_build_ingress_context_overrides_returns_none_for_empty_input() -> None:
    """Ingress helper should not emit empty override structures."""

    assert build_ingress_context_overrides() is None


def test_tool_profile_policy_overlay_roundtrip() -> None:
    """Policy overlay helper should expose tool-profile value for runtime filters."""

    runtime_metadata = build_tool_profile_policy_overlay_metadata(
        tool_profile="support_readonly"
    )
    assert resolve_tool_profile_policy_overlay_value(
        runtime_metadata=runtime_metadata
    ) == "support_readonly"

