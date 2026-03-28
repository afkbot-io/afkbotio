"""Helpers for deterministic CLI output assertions in tests."""

from __future__ import annotations

import re

from typer.main import Typer
from typer.testing import CliRunner
from click.testing import Result

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(value: str) -> str:
    """Return CLI output without ANSI escape sequences."""

    return _ANSI_RE.sub("", value)


def invoke_plain(
    runner: CliRunner,
    app: Typer,
    args: list[str],
    *,
    terminal_width: int = 140,
) -> tuple[Result, str]:
    """Invoke one CLI command with stable rendering for text assertions."""

    result = runner.invoke(app, args, color=False, terminal_width=terminal_width)
    text = result.stdout if result.stdout else result.output
    return result, strip_ansi(text)


def invoke_plain_help(
    runner: CliRunner,
    app: Typer,
    args: list[str],
    *,
    terminal_width: int = 140,
) -> tuple[Result, str]:
    """Invoke one help command with stable rendering for help-surface assertions."""

    return invoke_plain(runner, app, [*args, "--help"], terminal_width=terminal_width)
