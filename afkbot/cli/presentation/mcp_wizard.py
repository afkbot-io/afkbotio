"""Interactive helpers for MCP add-by-URL CLI flows."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.inline_select import confirm_space, run_inline_multi_select, select_option_dialog
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.services.mcp_integration.url_resolver import MCPURLResolution, resolve_mcp_url

_REMOTE_TRANSPORT_OPTIONS = ["http", "sse", "websocket"]
_CAPABILITY_OPTIONS: list[tuple[str, str]] = [
    ("tools", "tools"),
    ("resources", "resources"),
    ("prompts", "prompts"),
]


def mcp_wizard_enabled() -> bool:
    """Return whether interactive MCP wizard prompts can run in this terminal."""

    return supports_interactive_tty()


def prompt_mcp_url(*, default: str | None = None) -> str:
    """Prompt for the remote MCP endpoint URL."""

    prompt_default = (default or "").strip() or None
    return str(typer.prompt("MCP URL", default=prompt_default)).strip()


def prompt_resolved_mcp_url(*, default: str | None = None) -> MCPURLResolution:
    """Prompt until the operator provides one valid remote MCP URL."""

    prompt_default = (default or "").strip() or None
    while True:
        candidate = prompt_mcp_url(default=prompt_default)
        try:
            return resolve_mcp_url(candidate)
        except ValueError as exc:
            typer.echo(f"Invalid MCP URL: {exc}")
            prompt_default = candidate


def prompt_mcp_server(*, default: str) -> str:
    """Prompt for the normalized MCP server identifier."""

    return str(typer.prompt("Server id", default=default)).strip()


def prompt_mcp_transport(*, default: str) -> str:
    """Prompt for the transport used by the remote MCP endpoint."""

    return select_option_dialog(
        title="MCP Transport",
        text="Choose the remote transport for this MCP endpoint.",
        options=list(_REMOTE_TRANSPORT_OPTIONS),
        default=default,
        hint_text="Arrow keys move, Enter confirms. HTTP is the default for remote URL endpoints.",
    )


def prompt_mcp_capabilities(*, defaults: tuple[str, ...]) -> tuple[str, ...]:
    """Prompt for MCP capability visibility advertised to the IDE."""

    selected = run_inline_multi_select(
        title="MCP Capabilities",
        text="Select the capabilities exposed by this MCP server.",
        options=_CAPABILITY_OPTIONS,
        default_values=defaults or ("tools",),
        hint_text="Space toggles, Enter confirms, A selects all.",
    )
    if not selected:
        return defaults or ("tools",)
    return tuple(selected)


def prompt_optional_refs(
    *,
    label: str,
    suggestion: str,
    default_values: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Prompt for optional env/secret refs as a comma-separated list."""

    default_text = ", ".join(default_values)
    answer = str(
        typer.prompt(
            f"{label} (comma-separated, optional; example: {suggestion})",
            default=default_text,
            show_default=bool(default_text),
        )
    ).strip()
    if not answer:
        return ()
    return tuple(_split_refs(answer))


def confirm_mcp_add(*, preview_text: str) -> bool:
    """Confirm one MCP add/update after showing the preview block."""

    return confirm_space(
        question=preview_text,
        default=True,
        title="MCP Add",
        yes_label="Write config",
        no_label="Cancel",
        hint_text=(
            "This stores MCP profile config. Runtime access uses `mcp.tools.list` / "
            "`mcp.tools.call` for enabled remote `tools` servers with matching "
            "policy/network access."
        ),
    )


def confirm_mcp_remove(*, preview_text: str) -> bool:
    """Confirm one MCP removal after showing the preview block."""

    return confirm_space(
        question=preview_text,
        default=False,
        title="MCP Remove",
        yes_label="Remove config",
        no_label="Cancel",
        hint_text="This only updates IDE-side MCP profile config.",
    )


def render_mcp_add_preview(
    *,
    resolution: MCPURLResolution,
    preview_server_id: str,
    preview_transport: str,
    preview_capabilities: tuple[str, ...],
    preview_env_refs: tuple[str, ...],
    preview_secret_refs: tuple[str, ...],
    target_path: str,
    storage_mode: str,
    replacing_existing: bool,
    enabled: bool,
) -> str:
    """Render one human-readable preview before persisting an MCP config."""

    lines = [
        "Write remote MCP server config to the current profile?",
        "",
        f"- url: {resolution.url}",
        f"- server: {preview_server_id}",
        f"- transport: {preview_transport}",
        f"- capabilities: {', '.join(preview_capabilities) or 'none'}",
        f"- env_refs: {', '.join(preview_env_refs) or 'none'}",
        f"- secret_refs: {', '.join(preview_secret_refs) or 'none'}",
        f"- enabled: {'yes' if enabled else 'no'}",
        f"- storage_mode: {storage_mode}",
        f"- target_path: {target_path}",
        f"- replace_effective_server: {'yes' if replacing_existing else 'no'}",
        (
            "- boundary: Runtime MCP access uses `mcp.tools.list` / `mcp.tools.call` for "
            "enabled remote `tools` servers with matching policy/network access."
        ),
    ]
    return "\n".join(lines)


def render_mcp_remove_preview(
    *,
    server: str,
    transport: str,
    url: str | None,
    target_path: str,
    storage_mode: str,
    config_source: str | None,
) -> str:
    """Render one human-readable preview before removing an MCP config."""

    lines = [
        "Remove MCP server config from the current profile?",
        "",
        f"- server: {server}",
        f"- transport: {transport}",
        f"- url: {url or '-'}",
        f"- storage_mode: {storage_mode}",
        f"- target_path: {target_path}",
        f"- config_source: {config_source or '-'}",
        (
            "- boundary: Runtime MCP access uses `mcp.tools.list` / `mcp.tools.call` for "
            "enabled remote `tools` servers with matching policy/network access."
        ),
    ]
    return "\n".join(lines)


def _split_refs(answer: str) -> list[str]:
    return [item.strip() for item in answer.split(",") if item.strip()]
