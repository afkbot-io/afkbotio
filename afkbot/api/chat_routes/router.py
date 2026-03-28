"""Router assembly for chat HTTP and WebSocket endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from afkbot.api.chat_routes.contracts import SecureFieldSubmitResponse
from afkbot.api.chat_routes.http import (
    get_chat_catalog,
    get_chat_progress,
    post_chat_turn,
    post_question_answer,
    post_secure_field,
)
from afkbot.api.chat_routes.websocket import ws_chat_progress
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.api_runtime import ProgressPollResponse
from afkbot.services.app_catalog import AppCatalogResponse

router = APIRouter(prefix="/v1/chat", tags=["chat"])

router.add_api_route("/turn", post_chat_turn, methods=["POST"], response_model=TurnResult)
router.add_api_route("/catalog", get_chat_catalog, methods=["GET"], response_model=AppCatalogResponse)
router.add_api_route("/progress", get_chat_progress, methods=["GET"], response_model=ProgressPollResponse)
router.add_api_route("/secure-field", post_secure_field, methods=["POST"], response_model=SecureFieldSubmitResponse)
router.add_api_route("/answer", post_question_answer, methods=["POST"], response_model=TurnResult)
router.add_api_websocket_route("/progress/ws", ws_chat_progress)

__all__ = ["router"]
