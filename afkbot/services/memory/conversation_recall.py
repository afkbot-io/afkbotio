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
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_turn_repo import ChatTurnRepository
from afkbot.repositories.support import profile_exists
from afkbot.services.channel_routing.policy import is_user_facing_transport
from afkbot.services.session_ids import compose_bounded_session_id, encode_session_component
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
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationRecallTarget:
    """Normalized target session with optional ownership metadata."""

    session_id: str
    owner_profile_id: str | None


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
            compactions: ChatSessionCompactionRepository,
            turns: ChatTurnRepository,
            chat_sessions: ChatSessionRepository,
        ) -> list[ConversationRecallHit]:
            if await profile_exists(session, profile_id=profile_id) is None:
                raise ConversationRecallServiceError(
                    error_code="memory_profile_not_found",
                    reason=f"Profile '{profile_id}' does not exist.",
                )
            target_session = await chat_sessions.get(session_id)
            if target_session is None or target_session.profile_id != profile_id:
                return []
            candidates: list[tuple[tuple[int, int, int, int], ConversationRecallHit]] = []
            compaction = await compactions.get(profile_id=profile_id, session_id=session_id)
            compacted_until_turn_id = 0
            if compaction and compaction.summary_text.strip():
                compacted_until_turn_id = int(compaction.compacted_until_turn_id)
                compaction_score = self._score_text(query=normalized_query, text=compaction.summary_text)
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
            raw_turns = await turns.list_recent(profile_id=profile_id, session_id=session_id, limit=25)
            for turn in raw_turns:
                if compacted_until_turn_id and int(turn.id) <= compacted_until_turn_id:
                    continue
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
        actor_account_id: str | None = None,
        actor_peer_id: str | None = None,
        actor_thread_id: str | None = None,
        actor_user_id: str | None = None,
    ) -> list[ConversationRecallHit]:
        """Authorize one actor boundary, then search recall for the resolved target session."""

        normalized_actor_session_id = self._normalize_actor_session_id(actor_session_id)
        actor = ConversationRecallActor(
            session_id=normalized_actor_session_id,
            transport=self._normalize_transport(actor_transport),
            account_id=(
                self._normalize_selector_value(actor_account_id)
                or self._normalize_account_id(
                    actor_transport=actor_transport,
                    actor_session_id=normalized_actor_session_id,
                )
            ),
            peer_id=self._normalize_selector_value(actor_peer_id),
            thread_id=self._normalize_selector_value(actor_thread_id),
            user_id=self._normalize_selector_value(actor_user_id),
        )
        resolved_target = await self._resolve_target_session(
            profile_id=profile_id,
            actor=actor,
            target_session_id=target_session_id,
        )
        return await self.search(
            profile_id=profile_id,
            session_id=resolved_target.session_id,
            query=query,
            limit=limit,
        )

    async def shutdown(self) -> None:
        """Dispose owned engine resources when this service owns an engine."""

        if self._engine is not None:
            await self._engine.dispose()

    async def _with_repos(
        self,
        op: Callable[
            [AsyncSession, ChatSessionCompactionRepository, ChatTurnRepository, ChatSessionRepository],
            Awaitable[TValue],
        ],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            compactions = ChatSessionCompactionRepository(session)
            turns = ChatTurnRepository(session)
            chat_sessions = ChatSessionRepository(session)
            return await op(session, compactions, turns, chat_sessions)

    @staticmethod
    def _normalize_actor_session_id(value: str | None) -> str:
        normalized = " ".join((value or "").strip().split())
        if not normalized:
            raise ConversationRecallServiceError(
                error_code="memory_actor_session_required",
                reason="Session id is required for conversation recall.",
            )
        return normalized

    @staticmethod
    def _normalize_query(value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ConversationRecallServiceError(
                error_code="memory_query_empty",
                reason="Query must not be empty.",
            )
        return normalized

    @staticmethod
    def _normalize_transport(transport: str | None) -> str | None:
        normalized = (transport or "").strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_selector_value(value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_account_id(*, actor_transport: str | None, actor_session_id: str) -> str | None:
        normalized_transport = (actor_transport or "").strip().lower()
        if normalized_transport == "taskflow":
            if actor_session_id.startswith("taskflow:"):
                return actor_session_id.removeprefix("taskflow:")
            return None
        if normalized_transport == "automation":
            for prefix in ("automation-webhook-", "automation-cron-"):
                if actor_session_id.startswith(prefix):
                    suffix = actor_session_id.removeprefix(prefix)
                    return suffix.split("-", 1)[0] if suffix else None
            return None
        if normalized_transport == "cli":
            if actor_session_id.startswith("cli:"):
                return actor_session_id.removeprefix("cli:")
            return None
        return None

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        return max(1, min(int(limit), 20))

    async def _resolve_target_session(
        self,
        *,
        profile_id: str,
        actor: ConversationRecallActor,
        target_session_id: str | None,
    ) -> ConversationRecallTarget:
        normalized_target = " ".join((target_session_id or "").strip().split()) or actor.session_id
        if normalized_target == actor.session_id:
            return ConversationRecallTarget(session_id=normalized_target, owner_profile_id=profile_id)
        if is_user_facing_transport(actor.transport):
            if not self._is_target_allowed_for_trusted_actor(
                actor=actor,
                session_id=normalized_target,
                profile_id=profile_id,
            ):
                raise ConversationRecallServiceError(
                    error_code="memory_cross_scope_forbidden",
                    reason="Actor is not authorized to access recall for the requested session.",
                )
            return ConversationRecallTarget(session_id=normalized_target, owner_profile_id=profile_id)
        if actor.transport not in _TRUSTED_FOREIGN_RECALL_TRANSPORTS:
            raise ConversationRecallServiceError(
                error_code="memory_cross_scope_forbidden",
                reason="This runtime may not access recall from another session.",
            )
        owner_profile_id = await self._lookup_session_owner_profile_id(session_id=normalized_target)
        if owner_profile_id != profile_id:
            raise ConversationRecallServiceError(
                error_code="memory_cross_scope_forbidden",
                reason="Target session belongs to another profile.",
            )
        if not self._is_target_allowed_for_trusted_actor(
            actor=actor,
            session_id=normalized_target,
            profile_id=profile_id,
        ):
            raise ConversationRecallServiceError(
                error_code="memory_cross_scope_forbidden",
                reason="Actor is not authorized to access recall for the requested session.",
            )
        return ConversationRecallTarget(session_id=normalized_target, owner_profile_id=owner_profile_id)

    async def _lookup_session_owner_profile_id(self, *, session_id: str) -> str | None:
        async def _op(
            session: AsyncSession,
            _compactions: ChatSessionCompactionRepository,
            _turns: ChatTurnRepository,
            chat_sessions: ChatSessionRepository,
        ) -> str | None:
            row = await chat_sessions.get(session_id)
            return None if row is None else row.profile_id

        return await self._with_repos(_op)

    @staticmethod
    def _is_target_allowed_for_trusted_actor(
        *,
        actor: ConversationRecallActor,
        session_id: str,
        profile_id: str,
    ) -> bool:
        transport = actor.transport
        if transport == "taskflow":
            account_id = actor.account_id
            if not account_id:
                return False
            return session_id == f"taskflow:{account_id}"
        if transport == "automation":
            account_id = actor.account_id
            if not account_id:
                return False
            return session_id.startswith(f"automation-webhook-{account_id}-") or session_id.startswith(
                f"automation-cron-{account_id}-"
            )
        if transport == "cli":
            return True
        if not is_user_facing_transport(transport):
            return False

        account_id = actor.account_id
        peer_id = actor.peer_id
        if not account_id or not peer_id:
            return False

        target_components = ConversationRecallService._parse_user_facing_session_components(
            session_id=session_id,
            profile_id=profile_id,
        )
        actor_components = ConversationRecallService._parse_user_facing_session_components(
            session_id=actor.session_id,
            profile_id=profile_id,
        )

        if target_components is None or actor_components is None:
            expected_scoped_actor_session_id = compose_bounded_session_id(
                "profile",
                encode_session_component(profile_id),
                actor.session_id,
            )
            return session_id in {actor.session_id, expected_scoped_actor_session_id}

        expected_peer = encode_session_component(peer_id)
        if target_components.get("chat") != expected_peer:
            return False
        if actor_components.get("chat") != expected_peer:
            return False

        scope_components = ("thread", "user")
        actor_scope_keys = {
            component for component in scope_components if actor_components.get(component) is not None
        }
        target_scope_keys = {
            component for component in scope_components if target_components.get(component) is not None
        }
        if actor_scope_keys != target_scope_keys:
            return False
        for component in actor_scope_keys:
            if actor_components[component] != target_components[component]:
                return False
        return True

    @classmethod
    def _parse_user_facing_session_components(
        cls,
        *,
        session_id: str,
        profile_id: str,
    ) -> dict[str, str] | None:
        normalized_session = session_id.strip()
        if not normalized_session:
            return None

        scoped_prefix = compose_bounded_session_id("profile", encode_session_component(profile_id)) + ":"
        if normalized_session.startswith(scoped_prefix):
            normalized_session = normalized_session[len(scoped_prefix) :]

        parsed = cls._parse_session_components(normalized_session)
        if parsed is None:
            return None
        if "chat" not in parsed:
            return None
        return parsed

    @staticmethod
    def _parse_session_components(session_id: str) -> dict[str, str] | None:
        parts = session_id.split(":")
        if len(parts) < 2 or parts[0] != "chat":
            return None

        result: dict[str, str] = {"chat": parts[1]}
        index = 2
        while index + 1 < len(parts):
            key = parts[index]
            value = parts[index + 1]
            if key not in {"thread", "user"}:
                return None
            if key in result:
                return None
            result[key] = value
            index += 2
        if index != len(parts):
            return None
        return result

    @staticmethod
    def _tokenize(text: str) -> tuple[str, ...]:
        return tuple(token.lower() for token in _TOKEN_RE.findall(text))

    @classmethod
    def _score_text(cls, *, query: str, text: str) -> tuple[int, int] | None:
        query_tokens = cls._tokenize(query)
        if not query_tokens:
            return None
        text_tokens = set(cls._tokenize(text))
        token_hits = sum(1 for token in query_tokens if token in text_tokens)
        if token_hits <= 0:
            return None
        query_phrase = " ".join(query_tokens)
        text_phrase = " ".join(cls._tokenize(text))
        exact_phrase = 1 if query_phrase and query_phrase in text_phrase else 0
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
