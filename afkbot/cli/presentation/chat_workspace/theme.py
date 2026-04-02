"""Theme helpers for chat workspace prompt surfaces."""

from __future__ import annotations

from prompt_toolkit.styles import Style


def build_chat_workspace_style() -> Style:
    """Build the prompt-session style for the chat workspace."""

    return Style.from_dict(
        {
            "": "#f5f7fa",
            "workspace.assistant": "bg:default #e6ebf2",
            "workspace.user-text": "#f8fafc",
            "workspace.user-label": "#00e0ff bold",
            "workspace.user-separator": "#7d8591",
            "workspace.notice": "bg:default #8993a0",
            "workspace.detail": "bg:default #8d97a4",
            "workspace.plan-title": "bg:default #f5f7fa bold",
            "workspace.plan-text": "bg:default #e6ebf2",
            "workspace.thinking": "bg:default #00d7ff bold",
            "workspace.planning": "bg:default #ad7aff",
            "workspace.tool": "bg:default #ffb224 bold",
            "workspace.success": "bg:default #57d38c",
            "workspace.error": "bg:default #ff7a7a",
            "workspace.status-line": "bg:default #bfc6d1",
            "workspace.queue-line": "bg:default #9199a5",
            "workspace.footer-line": "#8993a0",
            "bottom-toolbar": "#8993a0",
            "bottom-toolbar.text": "#8993a0",
            "completion-menu": "bg:#1b1f24 #d7dde6",
            "completion-menu.completion": "bg:#1b1f24 #d7dde6",
            "completion-menu.completion.current": "bg:#00566d #f5fbff",
            "completion-menu.meta.completion": "bg:#1b1f24 #8691a0",
            "completion-menu.meta.completion.current": "bg:#00566d #dff6ff",
        }
    )
