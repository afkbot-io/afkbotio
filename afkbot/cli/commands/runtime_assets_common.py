"""Shared helpers for runtime asset CLI groups."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from afkbot.cli.command_errors import raise_usage_error


def resolve_inline_or_file_text(
    *,
    text: str | None,
    from_file: Path | None,
) -> str:
    """Resolve text payload from exactly one of inline text or local file."""

    if (text is None) == (from_file is None):
        raise_usage_error("Provide exactly one of --text or --from-file.")
    if text is not None:
        return text
    if from_file is None:
        raise AssertionError("from_file must be provided when text is absent")
    return from_file.read_text(encoding="utf-8")


def emit_structured_error(
    exc: Exception,
    *,
    default_error_code: str,
) -> None:
    """Render one structured JSON error payload for CLI-safe failures."""

    emit_command_error(exc, default_error_code=default_error_code, json_output=True)


def emit_command_error(
    exc: Exception,
    *,
    default_error_code: str,
    json_output: bool,
) -> None:
    """Render one CLI-safe error payload in JSON or human-readable form."""

    error_code = getattr(exc, "error_code", default_error_code)
    reason = getattr(exc, "reason", str(exc))
    if isinstance(exc, FileNotFoundError):
        error_code = "not_found"
        reason = str(exc)
    if not json_output:
        typer.echo(f"ERROR [{error_code}] {reason}")
        return
    typer.echo(
        json.dumps(
            {
                "ok": False,
                "error_code": str(error_code),
                "reason": str(reason),
            },
            ensure_ascii=True,
        )
    )
