"""Chat route package exports and compatibility helpers."""

from afkbot.api.chat_auth import status_code_for_connect_access_error, ws_close_reason
from afkbot.api.chat_routes.contracts import (
    ChatTurnRequest,
    QuestionAnswerRequest,
    SecureFieldSubmitRequest,
    SecureFieldSubmitResponse,
)
from afkbot.api.chat_routes.router import router
from afkbot.api.chat_routes.scope import build_http_invalid_chat_request
from afkbot.api.chat_routes.websocket import _schedule_ws_auth_revalidate_at

__all__ = [
    "ChatTurnRequest",
    "QuestionAnswerRequest",
    "SecureFieldSubmitRequest",
    "SecureFieldSubmitResponse",
    "_schedule_ws_auth_revalidate_at",
    "build_http_invalid_chat_request",
    "router",
    "status_code_for_connect_access_error",
    "ws_close_reason",
]
