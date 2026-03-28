"""Resolver from policy selection to deterministic profile policy values."""

from __future__ import annotations

from collections.abc import Iterable

from afkbot.services.policy.presets_catalog import get_preset, list_capability_specs, list_preset_levels
from afkbot.services.policy.presets_contracts import (
    PolicySelection,
    PolicyCapabilityId,
    PolicyPresetLevel,
    ResolvedPolicy,
)


def parse_preset_level(raw: str) -> PolicyPresetLevel:
    """Parse preset level string into enum with explicit error."""

    normalized = raw.strip().lower()
    aliases = {
        "light": PolicyPresetLevel.SIMPLE,
        "easy": PolicyPresetLevel.SIMPLE,
        "hard": PolicyPresetLevel.STRICT,
    }
    aliased = aliases.get(normalized)
    if aliased is not None:
        return aliased
    for level in list_preset_levels():
        if level.value == normalized:
            return level
    raise ValueError(f"Unknown policy preset: {raw}")


def parse_capability_ids(raw_values: Iterable[str]) -> tuple[PolicyCapabilityId, ...]:
    """Parse raw capability values into unique ordered enum tuple."""

    seen: set[PolicyCapabilityId] = set()
    resolved: list[PolicyCapabilityId] = []
    by_value = {item.id.value: item.id for item in list_capability_specs()}
    legacy_aliases = {
        PolicyCapabilityId.EMAIL.value: PolicyCapabilityId.APPS,
        PolicyCapabilityId.TELEGRAM.value: PolicyCapabilityId.APPS,
    }
    for raw in raw_values:
        value = str(raw).strip().lower()
        if not value:
            continue
        cap_id = by_value.get(value) or legacy_aliases.get(value)
        if cap_id is None:
            raise ValueError(f"Unknown policy capability: {raw}")
        if cap_id in seen:
            continue
        seen.add(cap_id)
        resolved.append(cap_id)
    return tuple(resolved)


def default_capabilities_for_preset(level: PolicyPresetLevel) -> tuple[PolicyCapabilityId, ...]:
    """Return default capability ids for one preset level."""

    return get_preset(level).default_capabilities


def capability_choice_items() -> tuple[tuple[str, str], ...]:
    """Return UI-ready capability option pairs `(value, label)` in stable order."""

    return tuple(
        (item.id.value, f"{item.label} - {item.description}") for item in list_capability_specs()
    )


def resolve_policy(
    *,
    selection: PolicySelection,
    available_tool_names: tuple[str, ...],
) -> ResolvedPolicy:
    """Resolve one policy selection into canonical runtime policy payload."""

    preset = get_preset(selection.preset)
    capability_ids = selection.capabilities
    selected_specs = {item.id: item for item in list_capability_specs() if item.id in capability_ids}

    allowed: list[str] = []
    for tool_name in sorted(set(available_tool_names)):
        for spec in selected_specs.values():
            if _matches_capability(tool_name=tool_name, tool_names=spec.tool_names, prefixes=spec.tool_prefixes):
                allowed.append(tool_name)
                break
    if PolicyCapabilityId.MCP in capability_ids:
        allowed.append("mcp.*")

    return ResolvedPolicy(
        enabled=selection.enabled,
        preset=selection.preset,
        capabilities=capability_ids,
        allowed_tools=tuple(sorted(set(allowed))),
        max_iterations_main=preset.max_iterations_main,
        max_iterations_subagent=preset.max_iterations_subagent,
    )


def _matches_capability(*, tool_name: str, tool_names: tuple[str, ...], prefixes: tuple[str, ...]) -> bool:
    if tool_name in tool_names:
        return True
    return any(tool_name.startswith(prefix) for prefix in prefixes)
