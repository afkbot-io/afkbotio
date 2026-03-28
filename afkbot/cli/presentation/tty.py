"""Shared TTY capability helpers for CLI presentation modules."""

from __future__ import annotations

import sys


def supports_interactive_tty() -> bool:
    """Return whether stdin/stdout support interactive prompt rendering."""

    return sys.stdin.isatty() and sys.stdout.isatty()
