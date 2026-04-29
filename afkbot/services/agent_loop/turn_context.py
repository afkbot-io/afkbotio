"""Runtime context overrides for one agent turn."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.agent_loop.planning_policy import ChatPlanningMode
from afkbot.services.agent_loop.thinking import PlanningMode, ToolAccessMode, combine_prompt_overlays
from afkbot.services.llm.reasoning import ThinkingLevel


@dataclass(frozen=True, slots=True)
class TurnContextOverrides:
    """Turn-scoped context additions supplied by ingress/routing layers."""

    runtime_metadata: dict[str, object] | None = None
    trusted_runtime_context: dict[str, object] | None = None
    cli_approval_surface_enabled: bool = False
    approved_tool_names: tuple[str, ...] | None = None
    prompt_overlay: str | None = None
    planning_mode: PlanningMode = "off"
    execution_planning_mode: ChatPlanningMode | None = None
    thinking_level: ThinkingLevel | None = None
    tool_access_mode: ToolAccessMode | None = None
    persist_turn: bool | None = None


def merge_turn_context_overrides(
    *parts: TurnContextOverrides | None,
) -> TurnContextOverrides | None:
    """Merge trusted turn overrides from multiple ingress/runtime sources."""

    merged_metadata: dict[str, object] = {}
    merged_trusted_runtime_context: dict[str, object] = {}
    cli_approval_surface_enabled = False
    merged_approved_tool_names: list[str] = []
    merged_prompt: str | None = None
    planning_mode: PlanningMode = "off"
    execution_planning_mode: ChatPlanningMode | None = None
    thinking_level: ThinkingLevel | None = None
    tool_access_mode: ToolAccessMode | None = None
    persist_turn: bool | None = None
    saw_value = False

    for part in parts:
        if part is None:
            continue
        saw_value = True
        if part.runtime_metadata:
            merged_metadata.update(part.runtime_metadata)
        if part.trusted_runtime_context:
            merged_trusted_runtime_context.update(part.trusted_runtime_context)
        if part.cli_approval_surface_enabled:
            cli_approval_surface_enabled = True
        _extend_unique_names(
            target=merged_approved_tool_names,
            source=part.approved_tool_names,
        )
        merged_prompt = combine_prompt_overlays(merged_prompt, part.prompt_overlay)
        if part.planning_mode != "off":
            planning_mode = part.planning_mode
        if part.execution_planning_mode is not None:
            execution_planning_mode = part.execution_planning_mode
        if part.thinking_level is not None:
            thinking_level = part.thinking_level
        if part.tool_access_mode is not None:
            tool_access_mode = part.tool_access_mode
        if part.persist_turn is not None:
            persist_turn = part.persist_turn

    if not saw_value:
        return None
    if (
        not merged_metadata
        and not merged_trusted_runtime_context
        and not cli_approval_surface_enabled
        and not merged_approved_tool_names
        and merged_prompt is None
        and planning_mode == "off"
        and execution_planning_mode is None
        and thinking_level is None
        and tool_access_mode is None
        and persist_turn is None
    ):
        return None
    return TurnContextOverrides(
        runtime_metadata=merged_metadata or None,
        trusted_runtime_context=merged_trusted_runtime_context or None,
        cli_approval_surface_enabled=cli_approval_surface_enabled,
        approved_tool_names=tuple(merged_approved_tool_names) or None,
        prompt_overlay=merged_prompt,
        planning_mode=planning_mode,
        execution_planning_mode=execution_planning_mode,
        thinking_level=thinking_level,
        tool_access_mode=tool_access_mode,
        persist_turn=persist_turn,
    )


def _extend_unique_names(*, target: list[str], source: tuple[str, ...] | None) -> None:
    """Append trimmed names preserving order and uniqueness."""

    if not source:
        return
    seen = set(target)
    for raw_name in source:
        name = str(raw_name).strip()
        if not name or name in seen:
            continue
        target.append(name)
        seen.add(name)
