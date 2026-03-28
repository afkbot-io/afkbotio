"""Pure helper functions used by turn-preparation runtime orchestration."""

from __future__ import annotations

from difflib import get_close_matches
import re

from afkbot.services.agent_loop.context_builder import ContextAssets
from afkbot.services.agent_loop.execution_posture import first_execution_blocker
from afkbot.services.agent_loop.skill_router import SkillRoute, matches_skill_trigger
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.skills.doctor import suggest_skill_install_hints, suggest_skill_repair_commands
from afkbot.services.tools.base import ToolCall, ToolResult


AUTOMATION_INTENT_RE = re.compile(
    r"(automation|automate|cron|schedule|webhook|trigger|автоматизац|автомат|расписан|вебхук|триггер|крон)",
    re.IGNORECASE,
)
EXPLICIT_SKILL_INVOKE_RE = re.compile(
    r"(?<!\S)(?:/skill\s+|[@/$])([a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)
EXPLICIT_SKILL_MATCH_CUTOFF = 0.86


def has_automation_intent(message: str) -> bool:
    """Return whether one user message explicitly targets automation operations."""

    text = message.strip()
    if not text:
        return False
    return AUTOMATION_INTENT_RE.search(text) is not None


def combine_trusted_runtime_notes(*parts: str | None) -> str | None:
    """Join trusted runtime note fragments while skipping empty values."""

    normalized = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    if not normalized:
        return None
    return "\n\n".join(normalized)


def explicit_name_mentions_from_triggers(
    *,
    message: str,
    trigger_map: dict[str, str] | None,
) -> set[str]:
    """Extract explicit canonical names using a trigger-to-canonical mapping."""

    if not trigger_map:
        return set()
    lowered_message = message.lower()
    mentions: set[str] = set()
    for trigger, canonical_name in trigger_map.items():
        normalized_trigger = str(trigger).strip().lower()
        normalized_canonical = str(canonical_name).strip()
        if not normalized_trigger or not normalized_canonical:
            continue
        if matches_skill_trigger(message=lowered_message, trigger=normalized_trigger):
            mentions.add(normalized_canonical)
    return mentions


def explicit_name_mentions(
    *,
    message: str,
    candidate_names: set[str] | None,
) -> set[str]:
    """Extract explicitly referenced canonical names from one user message."""

    if not candidate_names:
        return set()
    lowered_message = message.lower()
    mentions: set[str] = set()
    for name in candidate_names:
        normalized = str(name).strip().lower()
        if not normalized:
            continue
        if matches_skill_trigger(message=lowered_message, trigger=normalized):
            mentions.add(name)
    return mentions


def explicit_skill_invocations(
    *,
    message: str,
    trigger_map: dict[str, str] | None,
) -> set[str]:
    """Extract explicit skill syntax such as `/skill imap`, `/imap`, `@imap`, or `$imap`."""

    if not trigger_map:
        return set()
    mentions: set[str] = set()
    for match in EXPLICIT_SKILL_INVOKE_RE.finditer(message):
        raw_name = str(match.group(1) or "").strip().lower()
        if not raw_name:
            continue
        canonical = resolve_explicit_skill_name(raw_name=raw_name, trigger_map=trigger_map)
        if canonical:
            mentions.add(canonical)
    return mentions


def explicit_subagent_invocations(
    *,
    message: str,
    candidate_names: set[str] | None,
) -> set[str]:
    """Extract explicit subagent syntax such as `@researcher` or `/reviewer`."""

    if not candidate_names:
        return set()
    normalized_candidates = {
        str(name).strip().lower(): str(name).strip()
        for name in candidate_names
        if str(name).strip()
    }
    mentions: set[str] = set()
    for match in EXPLICIT_SKILL_INVOKE_RE.finditer(message):
        raw_name = str(match.group(1) or "").strip().lower()
        if not raw_name:
            continue
        canonical = normalized_candidates.get(raw_name)
        if canonical:
            mentions.add(canonical)
    return mentions


def resolve_explicit_skill_name(
    *,
    raw_name: str,
    trigger_map: dict[str, str],
) -> str | None:
    """Resolve one explicit skill token to a canonical skill name."""

    canonical = trigger_map.get(raw_name)
    if canonical:
        return canonical
    close_keys = get_close_matches(
        raw_name,
        list(trigger_map.keys()),
        n=5,
        cutoff=EXPLICIT_SKILL_MATCH_CUTOFF,
    )
    if not close_keys:
        return None
    canonical_matches: list[str] = []
    seen: set[str] = set()
    for key in close_keys:
        matched = trigger_map.get(key)
        if not matched or matched in seen:
            continue
        canonical_matches.append(matched)
        seen.add(matched)
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    return None


def explicit_skill_execution_maps(
    *,
    context_assets: ContextAssets,
    explicit_skill_mentions: set[str],
    available_tools: tuple[LLMToolDefinition, ...],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Return explicit executable skills satisfied by the current visible tool surface."""

    if not explicit_skill_mentions or not available_tools:
        return {}, {}

    available_tool_names = {tool.name for tool in available_tools}
    skill_map = {item.name: item for item in context_assets.skills}
    explicit_tool_map: dict[str, tuple[str, ...]] = {}
    explicit_app_map: dict[str, tuple[str, ...]] = {}

    for skill_name in sorted(explicit_skill_mentions):
        skill = skill_map.get(skill_name)
        if skill is None or not skill.available:
            continue
        if skill.manifest.execution_mode not in {"executable", "dispatch"}:
            continue
        routed_tool_names = [
            tool_name for tool_name in skill.manifest.tool_names if tool_name in available_tool_names
        ]
        if (
            skill.manifest.app_names
            and "app.run" in available_tool_names
            and "app.run" not in routed_tool_names
        ):
            routed_tool_names.append("app.run")
        if not routed_tool_names:
            continue
        explicit_tool_map[skill_name] = tuple(routed_tool_names)
        if skill.manifest.app_names:
            explicit_app_map[skill_name] = tuple(
                str(app_name).strip().lower()
                for app_name in skill.manifest.app_names
                if str(app_name).strip()
            )

    return explicit_tool_map, explicit_app_map


def enrich_runtime_metadata(
    *,
    runtime_metadata: dict[str, object] | None,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    explicit_enforceable_skill_mentions: set[str],
    explicit_subagent_mentions: set[str],
) -> dict[str, object] | None:
    """Merge skill and subagent routing metadata into the turn runtime metadata."""

    selected_skill_names = set(skill_route.selected_skill_names)
    if explicit_skill_mentions or explicit_subagent_mentions or skill_route.affinity_skill_names:
        enriched_metadata = dict(runtime_metadata or {})
        if explicit_skill_mentions:
            enriched_metadata["explicit_skill_requests"] = sorted(explicit_skill_mentions)
            enriched_metadata["explicit_skill_requests_enforceable"] = sorted(
                explicit_enforceable_skill_mentions,
            )
        if selected_skill_names:
            enriched_metadata["selected_skill_requests"] = sorted(selected_skill_names)
            inferred_skill_names = set(skill_route.inferred_skill_names)
            if inferred_skill_names:
                enriched_metadata["inferred_skill_requests"] = sorted(inferred_skill_names)
        if skill_route.affinity_skill_names:
            enriched_metadata["affinity_skill_requests"] = list(skill_route.affinity_skill_names)
        if explicit_subagent_mentions:
            enriched_metadata["explicit_subagent_requests"] = sorted(explicit_subagent_mentions)
        return enriched_metadata
    return runtime_metadata


def explicit_skill_runtime_guidance(
    *,
    context_assets: ContextAssets,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    profile_id: str,
) -> str:
    """Render runtime guidance for explicit skill-invocation edge cases."""

    if not explicit_skill_mentions:
        return ""

    selected = {
        item.name: item for item in context_assets.skills if item.name in skill_route.selected_skill_names
    }
    if not selected:
        return ""

    lines: list[str] = [
        "The user explicitly invoked one or more skills for this turn.",
        "Treat the selected skill contract as authoritative for execution mode and available surface.",
    ]
    for name in skill_route.selected_skill_names:
        item = selected.get(name)
        if item is None:
            continue
        missing = ", ".join(item.missing_requirements) if item.missing_requirements else "-"
        tools = ", ".join(item.manifest.tool_names) if item.manifest.tool_names else "-"
        apps = ", ".join(item.manifest.app_names) if item.manifest.app_names else "-"
        install_hints = suggest_skill_install_hints(item)
        repair_commands = suggest_skill_repair_commands(item, profile_id=profile_id)
        install_hint_text = "; ".join(install_hints) if install_hints else "-"
        repair_text = "; ".join(repair_commands) if repair_commands else "-"
        lines.append(
            f"- {name}: mode={item.manifest.execution_mode}, available={'yes' if item.available else 'no'}, tools={tools}, apps={apps}, missing={missing}, install_hints={install_hint_text}, repair_commands={repair_text}"
        )
    if not skill_route.has_executable_selection and skill_route.has_unavailable_selection:
        lines.append(
            "No executable skill surface is available for the explicit selection. Do not claim execution or fabricated results. Explain that the selected skill is advisory-only or unavailable, name the missing requirements when relevant, include install or repair hints when available, and suggest `afk skill doctor --profile "
            f"{profile_id}` for operator diagnostics."
        )
        lines.append(
            "If you answer without tool calls, your final response must be concrete: name the selected skill, list the missing requirements, and include the exact install_hints or repair_commands shown above when they are not '-'. Do not replace them with generic advice."
        )
    elif not skill_route.has_executable_selection and skill_route.has_explicit_selection:
        lines.append(
            "The explicit selection is advisory-only. Keep the selected skill as guidance, but do not hide the normal tool surface or fabricate that the skill executed directly."
        )
    return "\n".join(lines)


def explicit_skill_unavailable_message(
    *,
    context_assets: ContextAssets,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    profile_id: str,
    user_message: str,
) -> str | None:
    """Build deterministic fail-closed text for explicit unavailable skills."""

    if (
        not explicit_skill_mentions
        or skill_route.has_executable_selection
        or not skill_route.has_unavailable_selection
    ):
        return None
    selected = {
        item.name: item for item in context_assets.skills if item.name in skill_route.selected_skill_names
    }
    if not selected:
        return None

    cyrillic = bool(re.search(r"[А-Яа-яЁё]", user_message))
    parts: list[str] = []
    for name in skill_route.selected_skill_names:
        item = selected.get(name)
        if item is None:
            continue
        missing = ", ".join(item.missing_requirements) if item.missing_requirements else "-"
        install_hints = suggest_skill_install_hints(item)
        repair_commands = suggest_skill_repair_commands(item, profile_id=profile_id)
        if cyrillic:
            text = (
                f"Скилл `{name}` сейчас недоступен для выполнения. "
                f"Отсутствуют требования: {missing}."
            )
            if install_hints:
                text += " Установи зависимости: " + "; ".join(f"`{hint}`" for hint in install_hints) + "."
            if repair_commands:
                text += " Для диагностики или починки используй: " + "; ".join(
                    f"`{cmd}`" for cmd in repair_commands
                ) + "."
        else:
            text = (
                f"The selected skill `{name}` is currently unavailable. "
                f"Missing requirements: {missing}."
            )
            if install_hints:
                text += " Install dependencies with " + "; ".join(f"`{hint}`" for hint in install_hints) + "."
            if repair_commands:
                text += " Diagnose or repair with " + "; ".join(
                    f"`{cmd}`" for cmd in repair_commands
                ) + "."
        parts.append(text)
    return "\n\n".join(parts) or None


def planned_tools_final_message(
    *,
    user_message: str,
    tool_calls: list[ToolCall],
    tool_results: list[ToolResult],
) -> str:
    """Build deterministic final text for bridge flow with preplanned tool calls."""

    if not tool_results:
        return "No tool calls were executed. The request was not completed."
    blocked = first_execution_blocker(tool_calls=tool_calls, tool_results=tool_results)
    if blocked is not None:
        return blocked.message
    failed = [result for result in tool_results if not result.ok]
    if not failed:
        return f"Completed requested operations for: {user_message}"

    first = failed[0]
    error_code = (first.error_code or "tool_failed").strip()
    reason = (first.reason or "").strip()
    details = f"{error_code}: {reason}" if reason else error_code
    return "One or more requested operations failed. " f"First error: {details}"


def turn_plan_payload(
    *,
    machine_state: str,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    explicit_enforceable_skill_mentions: set[str],
    explicit_subagent_mentions: set[str],
    available_tools: tuple[LLMToolDefinition, ...],
    planned_tool_calls: list[ToolCall] | None,
    planning_mode: str,
    chat_planning_mode: str | None,
    execution_planning_enabled: bool,
    thinking_level: str,
    tool_access_mode: str,
    request_timeout_sec: float | None = None,
    wall_clock_budget_sec: float | None = None,
) -> dict[str, object]:
    """Build deterministic planning payload with skill-first debug metadata."""

    payload: dict[str, object] = {
        "state": machine_state,
        "explicit_skill_mentions": sorted(explicit_skill_mentions),
        "explicit_skill_mentions_enforceable": sorted(explicit_enforceable_skill_mentions),
        "selected_skill_names": list(skill_route.selected_skill_names),
        "inferred_skill_names": list(skill_route.inferred_skill_names),
        "explicit_subagent_mentions": sorted(explicit_subagent_mentions),
        "planning_mode": planning_mode,
        "chat_planning_mode": chat_planning_mode or "off",
        "execution_planning_enabled": execution_planning_enabled,
        "thinking_level": thinking_level,
        "tool_access_mode": tool_access_mode,
    }
    if request_timeout_sec is not None:
        payload["request_timeout_sec"] = round(float(request_timeout_sec), 3)
    if wall_clock_budget_sec is not None:
        payload["wall_clock_budget_sec"] = round(float(wall_clock_budget_sec), 3)
    if available_tools:
        payload["available_tools_after_filter"] = [tool.name for tool in available_tools]
    if planned_tool_calls:
        payload["planned_tool_names"] = [call.name for call in planned_tool_calls]
    return payload
