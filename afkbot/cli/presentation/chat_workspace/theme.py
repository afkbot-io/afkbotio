"""Theme helpers for chat workspace prompt surfaces."""

from __future__ import annotations

from prompt_toolkit.styles import Style


def build_chat_workspace_style() -> Style:
    """Build the fullscreen workspace terminal style."""

    return Style.from_dict(
        {
            "workspace.transcript-shell": "bg:#141414",
            "workspace.transcript": "bg:#141414 #d9d9d9",
            "workspace.assistant": "#e2e2e2",
            "workspace.user-label": "#59ff7a bold",
            "workspace.user-separator": "#9b9b9b",
            "workspace.user-text": "#f1f1f1",
            "workspace.notice": "#8f8f8f",
            "workspace.activity": "#8f8f8f",
            "workspace.system": "#8a8a8a",
            "workspace.progress-thinking": "#3fd7ff",
            "workspace.progress-planning": "#d58cff",
            "workspace.progress-tool": "#ffbf3f bold",
            "workspace.progress-success": "#8ee38e",
            "workspace.progress-error": "#ff7b7b",
            "workspace.progress-detail": "#8f8f8f",
            "workspace.plan-title": "#f1f1f1 bold",
            "workspace.plan-text": "#d9d9d9",
            "workspace.status-shell": "bg:#141414",
            "workspace.status-line": "bg:#141414 #d0d0d0",
            "workspace.queue-shell": "bg:#141414",
            "workspace.queue-line": "bg:#141414 #9e9e9e",
            "workspace.composer-shell": "bg:#2f2f2f",
            "workspace.composer-field": "bg:#2f2f2f #f1f1f1",
            "workspace.footer-shell": "bg:#141414",
            "workspace.footer-line": "bg:#141414 #7d7d7d",
            "workspace.overlay": "bg:#232323 #f1f1f1",
            "workspace.overlay.body": "bg:#232323 #f1f1f1",
            "completion-menu": "bg:#1f1f1f #d7d7d7",
            "completion-menu.completion": "bg:#1f1f1f #d7d7d7",
            "completion-menu.completion.current": "bg:#0e5a66 #ffffff",
            "completion-menu.meta.completion": "bg:#1f1f1f #8f8f8f",
            "completion-menu.meta.completion.current": "bg:#0e5a66 #dcefff",
            "scrollbar.background": "bg:#202020",
            "scrollbar.button": "bg:#4f4f4f",
        }
    )
