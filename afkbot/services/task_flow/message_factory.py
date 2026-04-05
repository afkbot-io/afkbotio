"""Helpers for composing Task Flow runtime messages and session ids."""

from __future__ import annotations


def task_session_id(*, task_id: str) -> str:
    """Build deterministic child session id for one task."""

    return f"taskflow:{task_id}"


def compose_task_message(prompt: str) -> str:
    """Compose one detached task prompt for AgentLoop."""

    return prompt.strip()
