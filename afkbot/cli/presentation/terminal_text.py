"""Sanitizers for untrusted terminal-facing text."""

from __future__ import annotations

import re

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_terminal_text(value: str) -> str:
    """Remove terminal control sequences and compact whitespace for status text."""

    return " ".join(sanitize_terminal_line(value).split())


def sanitize_terminal_line(value: str) -> str:
    """Remove terminal control sequences while preserving visible line spacing."""

    sanitized = _ANSI_OSC_RE.sub("", value)
    sanitized = _ANSI_CSI_RE.sub("", sanitized)
    sanitized = sanitized.replace("\x1b", "")
    sanitized = _CONTROL_RE.sub(" ", sanitized)
    return sanitized
