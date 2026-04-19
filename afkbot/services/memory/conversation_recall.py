"""Read-only conversation recall over compacted and recent chat history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.repositories.support import profile_exists
from afkbot.settings import Settings

TValue = TypeVar("TValue")
_SERVICES_BY_ROOT: dict[str, "ConversationRecallService"] = {}
_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9_]{2,}")
_TRUSTED_FOREIGN_RECALL_TRANSPORTS = frozenset({"cli", "automation", "taskflow"})


class ConversationRecallServiceError(Exception):
    """Application error for conversation recall methods."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ConversationRecallHit:
    """One conversation recall search hit."""

    kind: str
    session_id: str
    turn_id: int | None
    excerpt: str
    summary: str
    score: float


@dataclass(frozen=True, slots=True)
class ConversationRecallActor:
    """Normalized actor boundary for recall authorization."""

    session_id: str
    transport: str | None = None


class ConversationRecallService:
    """Search recent raw turns plus compaction summaries for one session."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    async def search(
        self,
        *,
        profile_id: str,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[ConversationRecallHit]:
        """Return recall hits from trusted compaction plus recent raw turns."""

        normalized_query = self._normalize_query(query)
        normalized_limit = self._normalize_limit(limit)

        async def _op(
            session: AsyncSession,
            turns: ChatTurnRepository,
            compactions: ChatSessionCompactionRepository,
        ) -> list[ConversationRecallHit]:
            if not await profile_exists(session, profile_id=profile_id):
                raise ConversationRecallServiceError(
                    error_code="profile_not_found",
                    reason="Profile not found",
                )
            compaction = await compactions.get(profile_id=profile_id, session_id=session_id)
            min_turn_id = 0 if compaction is None else compaction.compacted_until_turn_id
            raw_turns = await turns.list_range(
                profile_id=profile_id,
                session_id=session_id,
                min_turn_id_exclusive=min_turn_id,
            )
            candidates: list[tuple[tuple[int, int, int, int], ConversationRecallHit]] = []
            if compaction is not None and compaction.summary_text.strip():
                compaction_score = self._score_text(
                    query=normalized_query,
                    text=compaction.summary_text,
                )
                if compaction_score is not None:
                    candidates.append(
                        (
                            (-compaction_score[0], -compaction_score[1], 0, 0),
                            ConversationRecallHit(
                                kind="compaction",
                                session_id=session_id,
                                turn_id=None,
                                excerpt=self._clip(compaction.summary_text),
                                summary="Compacted session history",
                                score=float(compaction_score[0]) + (compaction_score[1] / 1000.0),
                            ),
                        )
                    )
            for turn in raw_turns:
                combined = f"User: {turn.user_message}\nAssistant: {turn.assistant_message}"
                turn_score = self._score_text(query=normalized_query, text=combined)
                if turn_score is None:
                    continue
                candidates.append(
                    (
                        (-turn_score[0], -turn_score[1], 1, -int(turn.id)),
                        ConversationRecallHit(
                            kind="turn",
                            session_id=session_id,
                            turn_id=int(turn.id),
                            excerpt=self._clip(combined),
                            summary=f"Turn {turn.id}",
                            score=float(turn_score[0]) + (turn_score[1] / 1000.0),
                        ),
                    )
                )
            candidates.sort(key=lambda item: item[0])
            return [hit for _key, hit in candidates[:normalized_limit]]

        return await self._with_repos(_op)

    async def search_for_actor(
        self,
        *,
        profile_id: str,
        actor_session_id: str,
        actor_transport: str | None,
        target_session_id: str | None,
        query: str,
        limit: int = 5,
    ) -> list[ConversationRecallHit]:
        """Authorize one actor boundary, then search recall for the resolved target session."""

        actor = ConversationRecallActor(
            session_id=self._normalize_query(actor_session_id),
            transport=self._normalize_transport(actor_transport),
        )
        resolved_session_id = self._resolve_target_session(
            actor=actor,
            target_session_id=target_session_id,
        )
        return await self.search(
            profile_id=profile_id,
            session_id=resolved_session_id,
            query=query,
            limit=limit,
        )

    async def shutdown(self) -> None:
        """Dispose owned async engine when created by the service."""

        if self._engine is None:
            return
        await self._engine.dispose()

    async def _with_repos(
        self,
        op: Callable[
            [AsyncSession, ChatTurnRepository, ChatSessionCompactionRepository],
            Awaitable[TValue],
        ],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            turns = ChatTurnRepository(session)
            compactions = ChatSessionCompactionRepository(session)
            return await op(session, turns, compactions)

    @staticmethod
    def _normalize_query(query: str) -> str:
        value = " ".join(query.strip().split())
        if not value:
            raise ConversationRecallServiceError(
                error_code="conversation_recall_invalid_query",
                reason="query is required",
            )
        return value

    @staticmethod
    def _normalize_transport(transport: str | None) -> str | None:
        normalized = (transport or "").strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        if limit < 1:
            raise ConversationRecallServiceError(
                error_code="conversation_recall_invalid_limit",
                reason="limit must be >= 1",
            )
        return min(limit, 20)

    @classmethod
    def _resolve_target_session(
        cls,
        *,
        actor: ConversationRecallActor,
        target_session_id: str | None,
    ) -> str:
        normalized_target = " ".join((target_session_id or "").strip().split()) or actor.session_id
        if normalized_target == actor.session_id:
            return normalized_target
        if actor.transport not in _TRUSTED_FOREIGN_RECALL_TRANSPORTS:
            raise ConversationRecallServiceError(
                error_code="memory_cross_scope_forbidden",
                reason="This runtime may not access recall from another session.",
            )
        return normalized_target

    @staticmethod
    def _tokenize(text: str) -> tuple[str, ...]:
        return tuple(token.lower() for token in _TOKEN_RE.findall(text))

    @classmethod
    def _score_text(cls, *, query: str, text: str) -> tuple[int, int] | None:
        query_tokens = cls._tokenize(query)
        if not query_tokens:
            return None
        haystack = text.lower()
        exact_phrase = 1 if query.lower() in haystack else 0
        token_hits = sum(1 for token in query_tokens if token in haystack)
        if exact_phrase == 0 and token_hits == 0:
            return None
        return (exact_phrase, token_hits)

    @staticmethod
    def _clip(text: str, *, max_chars: int = 280) -> str:
        single_line = " ".join(text.split())
        if len(single_line) <= max_chars:
            return single_line
        return single_line[: max_chars - 3].rstrip() + "..."


def get_conversation_recall_service(settings: Settings) -> ConversationRecallService:
    """Return cached conversation recall service for the current root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is not None:
        return service
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    service = ConversationRecallService(factory, engine=engine)
    _SERVICES_BY_ROOT[key] = service
    return service


def reset_conversation_recall_services() -> None:
    """Reset cached conversation recall services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_conversation_recall_services_async() -> None:
    """Reset cached conversation recall services and dispose owned engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
