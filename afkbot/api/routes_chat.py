"""Compatibility shim for chat HTTP/WS route exports."""

from afkbot.api.chat_routes import (
    ChatTurnRequest,
    QuestionAnswerRequest,
    SecureFieldSubmitRequest,
    SecureFieldSubmitResponse,
    _schedule_ws_auth_revalidate_at,
    build_http_invalid_chat_request,
    router,
    status_code_for_connect_access_error,
    ws_close_reason,
)

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
