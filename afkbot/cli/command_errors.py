"""Helpers for deterministic CLI error output."""

from __future__ import annotations

from typing import NoReturn

import typer


def raise_usage_error(message: str, *, code: int = 2) -> NoReturn:
    """Emit a plain-text usage error and terminate with the requested exit code."""

    typer.echo(message, err=True)
    raise typer.Exit(code=code)
