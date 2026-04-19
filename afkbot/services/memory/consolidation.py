"""Consolidation policy bridging extracted claims, archival memory, and core memory."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from afkbot.services.agent_loop.memory_extraction import ExtractedMemoryCandidate
from afkbot.services.memory.contracts import MemoryItemMetadata, MemoryKind
from afkbot.services.memory.profile_memory_service import get_profile_memory_service
from afkbot.settings import Settings

_GLOBAL_RE = re.compile(
    r"(по умолчанию|во всех чатах|везде|глобально|для всего профиля|for all chats|globally|by default)",
    re.IGNORECASE,
)
_CHAT_LOCAL_RE = re.compile(
    r"(в этом чате|для этого чата|только здесь|на эту сессию|for this chat|for this session)",
    re.IGNORECASE,
)
_TEMPORARY_RE = re.compile(
    r"(на сегодня|временно|пока что|for today|temporar|for now|на время задачи|until tomorrow|до завтра)",
    re.IGNORECASE,
)
_LANGUAGE_RE = re.compile(
    r"(по-русски|на русском|in russian|по-английски|на английском|in english)",
    re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r"(кратк|коротк|brief|concise|detailed|подробн|long form|без таблиц|no tables)",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"(my name is|меня зовут|call me)\s+([^\s,.!?;:]+)", re.IGNORECASE)
_TIMEZONE_RE = re.compile(r"(timezone is|таймзона|часовой пояс)\s+([A-Za-z/_+-]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MemoryConsolidationPlan:
    """Resolved write plan for one extracted durable memory candidate."""

    memory_key: str
    summary: str
    details_md: str
    memory_kind: MemoryKind
    promote_global: bool = False
    mirror_to_core: bool = False
    core_memory_key: str | None = None


class MemoryConsolidationService:
    """Central consolidation policy for archival/core memory interactions."""

    _MIRRORABLE_KINDS = frozenset({"fact", "preference", "decision"})

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @classmethod
    def plan_candidate(cls, candidate: ExtractedMemoryCandidate) -> MemoryConsolidationPlan:
        """Resolve one extracted candidate into archival/global/core write intent."""

        core_memory_key = cls._build_core_memory_key(
            text=candidate.source_text,
            memory_kind=candidate.memory_kind,
        )
        promote_global = cls._should_promote_globally(
            candidate.source_text,
            memory_kind=candidate.memory_kind,
        )
        mirror_to_core = cls._should_mirror_to_core(
            candidate.source_text,
            memory_kind=candidate.memory_kind,
            core_memory_key=core_memory_key,
            promote_global=promote_global,
        )
        return MemoryConsolidationPlan(
            memory_key=cls._build_archival_memory_key(
                text=candidate.source_text,
                summary=candidate.summary,
                memory_kind=candidate.memory_kind,
                core_memory_key=core_memory_key if mirror_to_core else None,
            ),
            summary=candidate.summary,
            details_md=candidate.details_md,
            memory_kind=candidate.memory_kind,
            promote_global=promote_global,
            mirror_to_core=mirror_to_core,
            core_memory_key=core_memory_key if mirror_to_core else None,
        )

    async def mirror_plan_to_core(
        self,
        *,
        profile_id: str,
        plan: MemoryConsolidationPlan,
        source: str,
        source_kind: str,
    ) -> object | None:
        """Safely mirror one resolved consolidation plan into pinned core memory."""

        if not self._settings.memory_core_enabled or not plan.mirror_to_core:
            return None
        return await get_profile_memory_service(self._settings).remember(
            profile_id=profile_id,
            memory_key=plan.core_memory_key or plan.memory_key,
            summary=plan.summary,
            details_md=plan.details_md,
            source=source,
            source_kind=source_kind,
            memory_kind=plan.memory_kind,
            priority=self._core_priority(plan.memory_kind),
            confidence=self._core_confidence(plan.memory_kind),
        )

    async def mirror_item_to_core(
        self,
        *,
        profile_id: str,
        item: MemoryItemMetadata,
        source: str,
        source_kind: str,
    ) -> object | None:
        """Safely mirror one promoted archival item into pinned core memory."""

        if not self._settings.memory_core_enabled or item.memory_kind not in self._MIRRORABLE_KINDS:
            return None
        return await get_profile_memory_service(self._settings).remember(
            profile_id=profile_id,
            memory_key=item.memory_key,
            content=item.content,
            summary=item.summary,
            details_md=item.details_md,
            source=source,
            source_kind=source_kind,
            memory_kind=item.memory_kind,
            priority=self._core_priority(item.memory_kind),
            confidence=self._core_confidence(item.memory_kind),
        )

    @staticmethod
    def _build_archival_memory_key(
        *,
        text: str,
        summary: str,
        memory_kind: MemoryKind,
        core_memory_key: str | None,
    ) -> str:
        if core_memory_key is not None:
            return core_memory_key
        digest = hashlib.sha1(summary.encode("utf-8")).hexdigest()[:16]  # noqa: S324
        if memory_kind == "decision" and _GLOBAL_RE.search(text):
            return f"global-decision-{digest}"
        return f"auto-{memory_kind}-{digest}"

    @staticmethod
    def _should_promote_globally(text: str, *, memory_kind: MemoryKind) -> bool:
        if _CHAT_LOCAL_RE.search(text) or _TEMPORARY_RE.search(text):
            return False
        if _GLOBAL_RE.search(text):
            return True
        if memory_kind == "decision" and re.search(
            r"(для проекта|project-wide|standardize|стандартиз)",
            text,
            re.IGNORECASE,
        ):
            return True
        return False

    @classmethod
    def _should_mirror_to_core(
        cls,
        text: str,
        *,
        memory_kind: MemoryKind,
        core_memory_key: str | None,
        promote_global: bool,
    ) -> bool:
        if core_memory_key is None:
            return False
        if _CHAT_LOCAL_RE.search(text) or _TEMPORARY_RE.search(text):
            return False
        if memory_kind == "preference":
            return promote_global
        if memory_kind == "fact":
            return True
        if memory_kind == "decision":
            return promote_global
        return False

    @staticmethod
    def _build_core_memory_key(*, text: str, memory_kind: MemoryKind) -> str | None:
        if memory_kind == "preference" and _LANGUAGE_RE.search(text):
            return "preferred_language"
        if memory_kind == "preference" and _STYLE_RE.search(text):
            return "preferred_response_style"
        if memory_kind == "fact" and _NAME_RE.search(text):
            return "user_display_name"
        if memory_kind == "fact" and _TIMEZONE_RE.search(text):
            return "preferred_timezone"
        if memory_kind == "decision" and _GLOBAL_RE.search(text):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]  # noqa: S324
            return f"global_decision_{digest}"
        return None

    @staticmethod
    def _core_priority(memory_kind: MemoryKind) -> int:
        return 90 if memory_kind == "preference" else 80

    @staticmethod
    def _core_confidence(memory_kind: MemoryKind) -> float:
        return 0.95 if memory_kind == "decision" else 0.9


_SERVICES_BY_ROOT: dict[str, MemoryConsolidationService] = {}


def get_memory_consolidation_service(settings: Settings) -> MemoryConsolidationService:
    """Return cached consolidation service for the current root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is not None:
        return service
    service = MemoryConsolidationService(settings)
    _SERVICES_BY_ROOT[key] = service
    return service


def reset_memory_consolidation_services() -> None:
    """Reset cached consolidation services for tests."""

    _SERVICES_BY_ROOT.clear()
