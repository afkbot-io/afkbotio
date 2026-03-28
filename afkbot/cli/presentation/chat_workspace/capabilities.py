"""Capability-catalog rendering helpers for the interactive chat workspace."""

from __future__ import annotations

from afkbot.services.chat_session.input_catalog import ChatInputCatalog

MCP_CHAT_BOUNDARY_NOTE = (
    "Runtime MCP access uses `mcp.tools.list` and `mcp.tools.call`; `$mcp...` entries here are "
    "discovery hints for enabled URL-backed `tools` servers with matching policy/network access."
)


def capability_catalog_summary(catalog: ChatInputCatalog | None) -> str:
    """Render one compact capability-catalog summary for prompt status surfaces."""

    if catalog is None:
        return "unavailable"
    return (
        f"skills={len(catalog.skill_names)}"
        f", subagents={len(catalog.subagent_names)}"
        f", apps={len(catalog.app_names)}"
        f", mcp={len(catalog.mcp_tool_names)}"
    )


def render_capability_catalog(*, catalog: ChatInputCatalog | None, section: str) -> str:
    """Render one human-readable capability catalog block."""

    if catalog is None:
        return "Capability catalog unavailable for this chat session yet."

    lines = ["Available capabilities"]
    if section in {"all", "skills"}:
        lines.append(f"- skills: {_render_name_list(catalog.skill_names)}")
    if section in {"all", "subagents"}:
        lines.append(f"- subagents: {_render_name_list(catalog.subagent_names)}")
    if section in {"all", "apps"}:
        lines.append(f"- apps: {_render_name_list(catalog.app_names)}")
    if section in {"all", "mcp"}:
        lines.append(f"- mcp_servers: {_render_name_list(catalog.mcp_server_names)}")
        lines.append(f"- mcp_tools: {_render_name_list(catalog.mcp_tool_names)}")
        lines.append(f"- mcp_boundary: {MCP_CHAT_BOUNDARY_NOTE}")
    return "\n".join(lines)


def _render_name_list(items: tuple[str, ...]) -> str:
    if not items:
        return "none"
    return ", ".join(items)
