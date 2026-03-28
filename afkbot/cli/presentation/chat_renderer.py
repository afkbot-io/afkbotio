"""Formatted assistant and error rendering for CLI chat."""

from __future__ import annotations

import json
import sys

from afkbot.services.agent_loop.action_contracts import TurnResult

_AGENT_HEADER = "\033[96mAFK Agent\033[0m"
_ERROR_HEADER = "\033[91mERROR\033[0m"
_RESET = "\033[0m"
_DIFF_ADD = "\033[92m"
_DIFF_REMOVE = "\033[91m"
_DIFF_HUNK = "\033[93m"
_DIFF_FILE = "\033[96m"
_DIFF_META = "\033[90m"


def render_chat_result(
    result: TurnResult,
    *,
    include_header: bool = True,
    leading_blank_line: bool = True,
    ansi: bool | None = None,
) -> str:
    """Render turn result into a role-styled CLI block."""

    use_ansi = sys.stdout.isatty() if ansi is None else ansi
    message = (result.envelope.message or "").strip()
    if _is_error(result, message):
        return _render_error_block(
            message,
            include_header=include_header,
            leading_blank_line=leading_blank_line,
            ansi=use_ansi,
        )
    return _render_agent_block(
        message,
        include_header=include_header,
        leading_blank_line=leading_blank_line,
        ansi=use_ansi,
    )


def _render_agent_block(
    message: str,
    *,
    include_header: bool,
    leading_blank_line: bool,
    ansi: bool,
) -> str:
    lines = _normalize_lines(message, ansi=ansi)
    body = "\n".join(f"  {line}" for line in lines)
    if include_header:
        body = _AGENT_HEADER + "\n" + body
    if leading_blank_line:
        return "\n" + body
    return body


def _render_error_block(
    message: str,
    *,
    include_header: bool,
    leading_blank_line: bool,
    ansi: bool,
) -> str:
    lines = _normalize_lines(message, ansi=ansi)
    parsed = _extract_error_payload(message)
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            lines = [lines[0] if lines else "Request failed"]
            for key, value in err.items():
                lines.append(f"{key}: {value}")
    body = "\n".join(f"  {line}" for line in lines)
    if include_header:
        body = _ERROR_HEADER + "\n" + body
    if leading_blank_line:
        return "\n" + body
    return body


def _normalize_lines(message: str, *, ansi: bool) -> list[str]:
    if not message:
        return ["(empty response)"]
    lines = [line.rstrip() for line in message.splitlines()] or ["(empty response)"]
    if not ansi:
        return lines
    return _render_markdown_for_terminal(lines)


def _render_markdown_for_terminal(lines: list[str]) -> list[str]:
    rendered: list[str] = []
    in_diff_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == "```diff":
            in_diff_block = True
            continue
        if in_diff_block and stripped == "```":
            in_diff_block = False
            continue
        if in_diff_block:
            rendered.append(_colorize_diff_line(line))
            continue
        rendered.append(line)
    return rendered or ["(empty response)"]


def _colorize_diff_line(line: str) -> str:
    leading = line[: len(line) - len(line.lstrip())]
    content = line[len(leading) :]
    if not content:
        return line
    if content.startswith(("---", "+++")):
        color = _DIFF_FILE
    elif content.startswith("@@"):
        color = _DIFF_HUNK
    elif content.startswith("+"):
        color = _DIFF_ADD
    elif content.startswith("-"):
        color = _DIFF_REMOVE
    else:
        color = _DIFF_META
    return f"{leading}{color}{content}{_RESET}"


def _is_error(result: TurnResult, message: str) -> bool:
    if result.envelope.action == "block":
        return True
    if message.lower().startswith("[error"):
        return True
    payload = _extract_error_payload(message)
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    return any(key in err for key in ("message", "type", "code", "param"))


def _extract_error_payload(message: str) -> dict[str, object] | None:
    brace_idx = message.find("{")
    if brace_idx < 0:
        return None
    candidate = message[brace_idx:]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
