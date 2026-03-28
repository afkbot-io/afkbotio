"""Runtime guidance and fail-closed messages for explicit skill selection."""

from __future__ import annotations

import re

from afkbot.services.agent_loop.context_builder import ContextAssets
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.skills.doctor import suggest_skill_install_hints, suggest_skill_repair_commands


def explicit_skill_runtime_guidance(
    *,
    context_assets: ContextAssets,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    profile_id: str,
) -> str:
    """Render runtime guidance for explicit skill invocation edge cases."""

    if not explicit_skill_mentions:
        return ""

    selected = {item.name: item for item in context_assets.skills if item.name in skill_route.selected_skill_names}
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
