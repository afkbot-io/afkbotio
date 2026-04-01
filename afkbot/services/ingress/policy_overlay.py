"""Runtime metadata helpers for ingress-level policy overlays."""

from __future__ import annotations

from collections.abc import Mapping

POLICY_OVERLAY_METADATA_KEY = "policy_overlay"
POLICY_OVERLAY_TOOL_PROFILE_KEY = "tool_profile"


def build_tool_profile_policy_overlay_metadata(*, tool_profile: str) -> dict[str, object]:
    """Build one policy-overlay metadata payload for tool profile narrowing."""

    return {
        POLICY_OVERLAY_METADATA_KEY: {
            POLICY_OVERLAY_TOOL_PROFILE_KEY: tool_profile,
        }
    }


def resolve_tool_profile_policy_overlay_value(
    *,
    runtime_metadata: dict[str, object] | None,
) -> object | None:
    """Extract one raw tool profile value from policy-overlay runtime metadata."""

    if not isinstance(runtime_metadata, dict):
        return None
    raw_policy_overlay = runtime_metadata.get(POLICY_OVERLAY_METADATA_KEY)
    if not isinstance(raw_policy_overlay, Mapping):
        return None
    return raw_policy_overlay.get(POLICY_OVERLAY_TOOL_PROFILE_KEY)

