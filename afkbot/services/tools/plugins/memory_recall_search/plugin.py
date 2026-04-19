"""Tool plugin for session-scoped conversation recall search."""

from __future__ import annotations

from pydantic import Field, field_validator

from afkbot.services.memory.conversation_recall import (
    ConversationRecallServiceError,
    get_conversation_recall_service,
)
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class ConversationRecallSearchParams(ToolParameters):
    """Parameters for historical conversation recall."""

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    session_id: str | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _normalize_session_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("session_id must be a string")
        normalized = " ".join(value.strip().split())
        return normalized or None


class MemoryRecallSearchTool(ToolBase):
    """Return ranked recall hits from compacted and recent conversation state."""

    name = "memory.recall.search"
    description = "Search compacted and recent conversation context for one session."
    tags = ("memory", "recall", "history")
    parameters_model = ConversationRecallSearchParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        typed = ConversationRecallSearchParams.model_validate(params.model_dump())
        try:
            items = await get_conversation_recall_service(self._settings).search_for_actor(
                profile_id=typed.effective_profile_id,
                actor_session_id=ctx.session_id,
                actor_transport=self._actor_transport(ctx),
                target_session_id=typed.session_id,
                query=typed.query,
                limit=typed.limit,
                actor_account_id=self._actor_account_id(ctx),
                actor_peer_id=self._actor_peer_id(ctx),
                actor_thread_id=self._actor_thread_id(ctx),
                actor_user_id=self._actor_user_id(ctx),
            )
        except ConversationRecallServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)

        payload_items = [
            {
                "kind": item.kind,
                "session_id": item.session_id,
                "turn_id": item.turn_id,
                "excerpt": item.excerpt,
                "summary": item.summary,
                "score": item.score,
            }
            for item in items
        ]
        return ToolResult(ok=True, payload={"items": payload_items})

    @staticmethod
    def _actor_transport(ctx: ToolContext) -> str | None:
        metadata = ctx.runtime_metadata or {}
        transport = metadata.get("transport")
        return transport.strip() if isinstance(transport, str) else None

    @staticmethod
    def _actor_account_id(ctx: ToolContext) -> str | None:
        metadata = ctx.runtime_metadata or {}
        account_id = metadata.get("account_id")
        if not isinstance(account_id, str):
            return None
        normalized = account_id.strip()
        return normalized or None

    @staticmethod
    def _actor_peer_id(ctx: ToolContext) -> str | None:
        metadata = ctx.runtime_metadata or {}
        peer_id = metadata.get("peer_id")
        if not isinstance(peer_id, str):
            return None
        normalized = peer_id.strip()
        return normalized or None

    @staticmethod
    def _actor_thread_id(ctx: ToolContext) -> str | None:
        metadata = ctx.runtime_metadata or {}
        thread_id = metadata.get("thread_id")
        if not isinstance(thread_id, str):
            return None
        normalized = thread_id.strip()
        return normalized or None

    @staticmethod
    def _actor_user_id(ctx: ToolContext) -> str | None:
        metadata = ctx.runtime_metadata or {}
        user_id = metadata.get("user_id")
        if not isinstance(user_id, str):
            return None
        normalized = user_id.strip()
        return normalized or None


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.recall.search tool instance."""

    return MemoryRecallSearchTool(settings=settings)
