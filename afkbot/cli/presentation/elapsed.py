"""Elapsed-time formatting helpers for CLI progress surfaces."""

from __future__ import annotations


def format_elapsed_seconds(seconds: float) -> str:
    """Render elapsed seconds in the compact chat progress format."""

    elapsed_seconds = max(0, int(seconds))
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s"
    if elapsed_seconds < 3_600:
        minutes, remainder = divmod(elapsed_seconds, 60)
        return f"{minutes}m {remainder:02d}s"
    hours, remainder = divmod(elapsed_seconds, 3_600)
    minutes, remainder_seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {remainder_seconds:02d}s"
