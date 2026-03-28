"""In-memory session affinity for follow-up turns that continue the same skill workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FOLLOWUP_HINT_RE = re.compile(
    r"\b(?:again|same|still|through|with that|ещ[её]|через|этим|этот|тот же|снова|дальше|продолж\w*|теперь)\b",
    re.IGNORECASE,
)
_SHORT_FOLLOWUP_PHRASES = {
    "ага",
    "да",
    "давай",
    "ок",
    "окей",
    "конечно",
    "подтверждаю",
    "yes",
    "y",
    "sure",
    "continue",
    "go ahead",
}


@dataclass(slots=True)
class SessionSkillAffinityRecord:
    """Affinity snapshot for one session."""

    skill_names: tuple[str, ...]
    generation: int


class SessionSkillAffinityService:
    """Keep short-lived skill affinity for follow-up turns in one process."""

    def __init__(self, *, max_turn_gap: int = 3, max_sessions: int = 1024) -> None:
        self._max_turn_gap = max(1, max_turn_gap)
        self._max_sessions = max(1, max_sessions)
        self._session_generations: dict[tuple[str, str], int] = {}
        self._records: dict[tuple[str, str], SessionSkillAffinityRecord] = {}

    def resolve(
        self,
        *,
        profile_id: str,
        session_id: str,
        raw_user_message: str,
        explicit_skill_names: set[str],
        selected_skill_names: set[str],
    ) -> set[str]:
        """Return sticky skill names to reuse for a short follow-up turn."""

        if explicit_skill_names or selected_skill_names:
            return set()
        key = (profile_id, session_id)
        record = self._records.get(key)
        if record is None:
            return set()
        current_generation = self._session_generations.get(key, 0)
        if current_generation - record.generation > self._max_turn_gap:
            self._evict_key(key)
            return set()
        if not _looks_like_followup(raw_user_message):
            return set()
        return set(record.skill_names)

    def remember(
        self,
        *,
        profile_id: str,
        session_id: str,
        selected_skill_names: tuple[str, ...],
    ) -> None:
        """Update or clear session affinity after one turn preparation."""

        key = (profile_id, session_id)
        generation = self._session_generations.get(key, 0) + 1
        self._session_generations[key] = generation
        if selected_skill_names:
            self._records.pop(key, None)
            self._records[key] = SessionSkillAffinityRecord(
                skill_names=selected_skill_names,
                generation=generation,
            )
            self._evict_oldest_if_needed()
            return
        self._evict_key(key)

    def _evict_key(self, key: tuple[str, str]) -> None:
        self._records.pop(key, None)
        self._session_generations.pop(key, None)

    def _evict_oldest_if_needed(self) -> None:
        while len(self._records) > self._max_sessions:
            oldest_key = next(iter(self._records))
            self._evict_key(oldest_key)


def _looks_like_followup(message: str) -> bool:
    text = " ".join(message.split()).strip()
    if not text:
        return False
    if _FOLLOWUP_HINT_RE.search(text) is not None:
        return True
    return text.casefold() in _SHORT_FOLLOWUP_PHRASES
