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
    def _normalize_session_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class MemoryRecallSearchTool(ToolBase):
    """Search trusted compaction plus recent raw turns for one session."""

    name = "memory.recall.search"
    description = "Search historical conversation context for the active session."
    parameters_model = ConversationRecallSearchParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(
            ctx=ctx,
            params=params,
            expected=ConversationRecallSearchParams,
        )
        if isinstance(prepared, ToolResult):
            return prepared
        try:
            items = await get_conversation_recall_service(self._settings).search_for_actor(
                profile_id=ctx.profile_id,
                actor_session_id=ctx.session_id,
                actor_transport=self._actor_transport(ctx),
                target_session_id=prepared.session_id,
                query=prepared.query,
                limit=prepared.limit,
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


def create_tool(settings: Settings) -> ToolBase:
    """Create memory.recall.search tool instance."""

    return MemoryRecallSearchTool(settings=settings)
