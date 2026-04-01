"""Tests for chat workspace status/footer composition helpers."""

from __future__ import annotations

from afkbot.cli.presentation.chat_workspace.toolbar import (
    DEFAULT_CHAT_WORKSPACE_FOOTER,
    build_chat_workspace_footer,
    build_chat_workspace_status_line,
)
from afkbot.services.chat_session.activity_state import ChatActivitySnapshot
from afkbot.services.chat_session.session_state import ChatReplSessionState


def test_build_chat_workspace_status_line_includes_activity_detail() -> None:
    """Working status should expose activity detail such as current iteration."""

    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
        active_turn=True,
        latest_activity=ChatActivitySnapshot(
            stage="thinking",
            summary="thinking",
            detail="iteration 2",
            running=True,
        ),
    )

    rendered = build_chat_workspace_status_line(state)

    assert "• Working" in rendered
    assert "thinking..." in rendered


def test_build_chat_workspace_footer_includes_cwd_token(
    monkeypatch,  # noqa: ANN001
) -> None:
    """Footer should expose the active working directory token."""

    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.toolbar.os.getcwd",
        lambda: "/tmp/workspace",
    )
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    rendered = build_chat_workspace_footer(state)

    assert rendered.startswith(DEFAULT_CHAT_WORKSPACE_FOOTER)
    assert "cwd=/tmp/workspace" in rendered
