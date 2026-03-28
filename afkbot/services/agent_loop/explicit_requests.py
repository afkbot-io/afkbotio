"""Parsing and visibility helpers for explicit skill and subagent requests."""

from __future__ import annotations

from difflib import get_close_matches
import re

from afkbot.services.agent_loop.context_builder import ContextAssets
from afkbot.services.agent_loop.skill_router import matches_skill_trigger
from afkbot.services.llm.contracts import LLMToolDefinition

_EXPLICIT_SKILL_INVOKE_RE = re.compile(
    r"(?<!\S)(?:/skill\s+|[@/$])([a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)
_EXPLICIT_SKILL_REFERENCE_RE = re.compile(
    r"(?:(?<!\S)(?:use|using|invoke|run|try)(?:\s+the)?(?:\s+skill)?\s+|"
    r"(?<!\S)(?:используй|использовать|вызови|запусти)(?:\s+скилл)?\s+)"
    r"([a-z0-9][a-z0-9-]*)\b",
    re.IGNORECASE,
)
_EXPLICIT_SKILL_MATCH_CUTOFF = 0.86


def explicit_skill_references(
    *,
    message: str,
    trigger_map: dict[str, str] | None,
) -> set[str]:
    """Extract natural-language explicit skill references like `use foo`."""

    if not trigger_map:
        return set()
    mentions: set[str] = set()
    for match in _EXPLICIT_SKILL_REFERENCE_RE.finditer(message):
        raw_name = str(match.group(1) or "").strip().lower()
        if not raw_name:
            continue
        canonical = _resolve_explicit_skill_name(raw_name=raw_name, trigger_map=trigger_map)
        if canonical:
            mentions.add(canonical)
    return mentions


def explicit_name_mentions(
    *,
    message: str,
    candidate_names: set[str] | None,
) -> set[str]:
    """Extract explicitly referenced names from user message."""

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
    """Extract explicit skill invoke syntax like /skill imap, /imap, @imap, or $imap."""

    if not trigger_map:
        return set()
    mentions: set[str] = set()
    for match in _EXPLICIT_SKILL_INVOKE_RE.finditer(message):
        raw_name = str(match.group(1) or "").strip().lower()
        if not raw_name:
            continue
        canonical = _resolve_explicit_skill_name(raw_name=raw_name, trigger_map=trigger_map)
        if canonical:
            mentions.add(canonical)
    return mentions


def explicit_subagent_invocations(
    *,
    message: str,
    candidate_names: set[str] | None,
) -> set[str]:
    """Extract explicit subagent invoke syntax like @researcher or /reviewer."""

    if not candidate_names:
        return set()
    normalized_candidates = {
        str(name).strip().lower(): str(name).strip()
        for name in candidate_names
        if str(name).strip()
    }
    mentions: set[str] = set()
    for match in _EXPLICIT_SKILL_INVOKE_RE.finditer(message):
        raw_name = str(match.group(1) or "").strip().lower()
        if not raw_name:
            continue
        canonical = normalized_candidates.get(raw_name)
        if canonical:
            mentions.add(canonical)
    return mentions


def visible_executable_explicit_skills(
    *,
    context_assets: ContextAssets,
    explicit_skill_mentions: set[str],
    available_tools: tuple[LLMToolDefinition, ...],
    visible_enforceable_skill_names: set[str],
) -> set[str]:
    """Return explicit executable skills that still have a visible runtime surface."""

    if not explicit_skill_mentions or not available_tools:
        return set()

    available_tool_names = {tool.name for tool in available_tools}
    skill_map = {item.name: item for item in context_assets.skills}
    enforceable: set[str] = set()

    for skill_name in explicit_skill_mentions:
        skill = skill_map.get(skill_name)
        if skill is None or not skill.available:
            continue
        if skill.manifest.execution_mode not in {"executable", "dispatch"}:
            continue
        if skill_name in visible_enforceable_skill_names:
            enforceable.add(skill_name)
            continue
        if any(tool_name in available_tool_names for tool_name in skill.manifest.tool_names):
            enforceable.add(skill_name)
    return enforceable


def _resolve_explicit_skill_name(
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
        cutoff=_EXPLICIT_SKILL_MATCH_CUTOFF,
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
