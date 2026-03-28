"""Metadata-driven skill routing for skill-first execution."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.skills.skills import SkillInfo

_LOW_SPECIFICITY_TRIGGER_RE = re.compile(r"^[\w-]+$", re.IGNORECASE)
_TASK_CONTEXT_RE = re.compile(
    r"\b(?:send|create|open|review|show|get|check|run|use|inspect|install|fix|continue|search|find|"
    r"manage|navigate|click|fill|screenshot|extract|convert|edit|update|delete|list|"
    r"отправ|созда|откро|покаж|получ|проверь|запуст|использ|посмотр|изуч|найд|установ|настро|"
    r"продолж|перейд|клик|заполн|сдела|обнов|удал|список|прочита|отредакт|созвон)\w*\b",
    re.IGNORECASE,
)
_DESCRIPTIVE_CONTEXT_RE = re.compile(
    r"\b(?:supports?|includes?|has|provides?|features?|offers?|contains?|allows?|available|"
    r"platform|service|runtime|cli|agent|bot|bots|channels?|integrations?|automations?|"
    r"поддержива|включа|умеет|имеет|содерж|доступн|платформ|сервис|рантайм|cli|агент|бот|"
    r"канал|интеграц|автоматизац)\w*\b",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(r"[,.!?:;\n()]")


def matches_skill_trigger(
    *,
    message: str,
    trigger: str,
    require_task_context: bool = False,
) -> bool:
    """Return whether one trigger appears as a standalone token in message."""

    normalized_message = message.lower()
    normalized_trigger = trigger.strip().lower()
    if not normalized_trigger:
        return False
    pattern = re.compile(rf"(?<![\w-]){re.escape(normalized_trigger)}(?![\w-])")
    for match in pattern.finditer(normalized_message):
        if not require_task_context or not _is_low_specificity_trigger(normalized_trigger):
            return True
        if _has_task_context_near_trigger(
            message=normalized_message,
            trigger_start=match.start(),
            trigger_end=match.end(),
        ):
            return True
    return False


@dataclass(frozen=True, slots=True)
class SkillRoute:
    """Resolved skill selection and derived execution constraints for one turn."""

    selected_skill_names: tuple[str, ...]
    executable_skill_names: tuple[str, ...]
    advisory_skill_names: tuple[str, ...]
    unavailable_skill_names: tuple[str, ...]
    unavailable_blocking_skill_names: tuple[str, ...]
    explicit_skill_names: tuple[str, ...]
    affinity_skill_names: tuple[str, ...]
    inferred_skill_names: tuple[str, ...]
    tool_names: tuple[str, ...]
    app_names: tuple[str, ...]
    preferred_tool_order: tuple[str, ...]

    @property
    def has_selection(self) -> bool:
        """Return whether at least one skill was selected for this turn."""

        return bool(self.selected_skill_names)

    @property
    def has_executable_selection(self) -> bool:
        """Return whether selected skills include at least one executable surface."""

        return bool(self.executable_skill_names)

    @property
    def has_explicit_selection(self) -> bool:
        """Return whether the user explicitly invoked one or more skills this turn."""

        return bool(self.explicit_skill_names)

    @property
    def has_unavailable_selection(self) -> bool:
        """Return whether selected skills include any unavailable skill."""

        return bool(self.unavailable_skill_names)

    @property
    def has_unavailable_blocking_selection(self) -> bool:
        """Return whether selected skills include unavailable executable/dispatch skills."""

        return bool(self.unavailable_blocking_skill_names)


class SkillRouter:
    """Select relevant skills from message text and skill manifests."""

    def route(
        self,
        *,
        message: str,
        skills: tuple[SkillInfo, ...] | list[SkillInfo],
        explicit_skill_names: set[str] | None = None,
        affinity_skill_names: set[str] | None = None,
    ) -> SkillRoute:
        """Return selected skills and derived tool/app constraints for one turn."""

        all_skills = list(skills)
        available_skills = [item for item in all_skills if item.available]
        explicit = {
            name.strip()
            for name in (explicit_skill_names or set())
            if name and name.strip()
        }
        affinity = {
            name.strip()
            for name in (affinity_skill_names or set())
            if name and name.strip()
        }

        selected: set[str] = set(explicit) | set(affinity)
        lowered_message = message.lower()
        for skill in available_skills:
            if skill.name in selected:
                continue
            if self._matches_any_trigger(lowered_message, skill.manifest.triggers):
                selected.add(skill.name)

        ordered_selected = tuple(
            skill.name for skill in all_skills if skill.name in selected
        )
        ordered_explicit = tuple(
            skill_name
            for skill_name in ordered_selected
            if skill_name in explicit
        )
        ordered_affinity = tuple(
            skill_name
            for skill_name in ordered_selected
            if skill_name in affinity and skill_name not in explicit
        )
        ordered_inferred = tuple(
            skill_name
            for skill_name in ordered_selected
            if skill_name not in explicit and skill_name not in affinity
        )

        selected_skill_set = set(ordered_selected)
        selected_skills = [
            skill for skill in all_skills if skill.name in selected_skill_set
        ]
        executable_skills = [
            skill
            for skill in selected_skills
            if skill.available and skill.manifest.execution_mode in {"executable", "dispatch"}
        ]
        advisory_skills = [
            skill
            for skill in selected_skills
            if skill.available and skill.manifest.execution_mode == "advisory"
        ]
        unavailable_skills = [
            skill
            for skill in selected_skills
            if not skill.available
        ]
        unavailable_blocking_skills = [
            skill
            for skill in unavailable_skills
            if skill.manifest.execution_mode in {"executable", "dispatch"}
        ]
        return SkillRoute(
            selected_skill_names=ordered_selected,
            executable_skill_names=tuple(skill.name for skill in executable_skills),
            advisory_skill_names=tuple(skill.name for skill in advisory_skills),
            unavailable_skill_names=tuple(skill.name for skill in unavailable_skills),
            unavailable_blocking_skill_names=tuple(
                skill.name for skill in unavailable_blocking_skills
            ),
            explicit_skill_names=ordered_explicit,
            affinity_skill_names=ordered_affinity,
            inferred_skill_names=ordered_inferred,
            tool_names=self._merge_manifest_lists(
                executable_skills,
                field_name="tool_names",
            ),
            app_names=self._merge_manifest_lists(
                executable_skills,
                field_name="app_names",
            ),
            preferred_tool_order=self._merge_manifest_lists(
                executable_skills,
                field_name="preferred_tool_order",
            ),
        )

    @staticmethod
    def _merge_manifest_lists(
        skills: list[SkillInfo],
        *,
        field_name: str,
    ) -> tuple[str, ...]:
        """Merge one manifest tuple field while preserving first-seen order."""

        merged: list[str] = []
        seen: set[str] = set()
        for skill in skills:
            raw_values = getattr(skill.manifest, field_name, ())
            for raw_value in raw_values:
                value = str(raw_value).strip().lower()
                if not value or value in seen:
                    continue
                merged.append(value)
                seen.add(value)
        return tuple(merged)

    @staticmethod
    def _matches_any_trigger(message: str, triggers: tuple[str, ...]) -> bool:
        """Return whether any manifest trigger matches the user message."""

        for raw_trigger in triggers:
            if matches_skill_trigger(
                message=message,
                trigger=str(raw_trigger),
                require_task_context=True,
            ):
                return True
        return False


def _is_low_specificity_trigger(trigger: str) -> bool:
    """Return whether one trigger is generic enough to require task-shaped context."""

    return " " not in trigger and _LOW_SPECIFICITY_TRIGGER_RE.fullmatch(trigger) is not None


def _has_task_context_near_trigger(
    *,
    message: str,
    trigger_start: int,
    trigger_end: int,
) -> bool:
    """Require local task wording for generic one-token implicit skill triggers."""

    if len(message.split()) <= 6:
        return True

    clause_start = 0
    for match in _CLAUSE_BOUNDARY_RE.finditer(message):
        if match.end() <= trigger_start:
            clause_start = match.end()
            continue
        clause_end = match.start()
        break
    else:
        clause_end = len(message)

    clause = message[clause_start:clause_end].strip()
    if not clause:
        return False
    if _TASK_CONTEXT_RE.search(clause) is not None:
        return True
    if _DESCRIPTIVE_CONTEXT_RE.search(clause) is not None:
        return False

    window_start = max(0, trigger_start - 48)
    window_end = min(len(message), trigger_end + 48)
    return _TASK_CONTEXT_RE.search(message[window_start:window_end]) is not None
