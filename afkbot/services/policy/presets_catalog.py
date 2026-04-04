"""Catalog of setup/profile policy presets and capability definitions."""

from __future__ import annotations

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.policy.presets_contracts import (
    PolicyCapabilityId,
    PolicyCapabilitySpec,
    PolicyPresetLevel,
    PolicyPresetSpec,
)

CAPABILITY_ORDER: tuple[PolicyCapabilityId, ...] = (
    PolicyCapabilityId.FILES,
    PolicyCapabilityId.SHELL,
    PolicyCapabilityId.MEMORY,
    PolicyCapabilityId.CREDENTIALS,
    PolicyCapabilityId.SUBAGENTS,
    PolicyCapabilityId.AUTOMATION,
    PolicyCapabilityId.HTTP,
    PolicyCapabilityId.WEB,
    PolicyCapabilityId.BROWSER,
    PolicyCapabilityId.SKILLS,
    PolicyCapabilityId.APPS,
    PolicyCapabilityId.MCP,
    PolicyCapabilityId.DEBUG,
)

CAPABILITIES: dict[PolicyCapabilityId, PolicyCapabilitySpec] = {
    PolicyCapabilityId.FILES: PolicyCapabilitySpec(
        id=PolicyCapabilityId.FILES,
        label="File operations",
        description="Read/list/search/write/edit filesystem files.",
        tool_names=("diffs.render",),
        tool_prefixes=("file.",),
    ),
    PolicyCapabilityId.SHELL: PolicyCapabilitySpec(
        id=PolicyCapabilityId.SHELL,
        label="Shell execution",
        description="Execute shell commands under policy restrictions.",
        tool_names=("bash.exec",),
    ),
    PolicyCapabilityId.MEMORY: PolicyCapabilitySpec(
        id=PolicyCapabilityId.MEMORY,
        label="Memory",
        description="Search and store profile memory.",
        tool_prefixes=("memory.",),
    ),
    PolicyCapabilityId.CREDENTIALS: PolicyCapabilitySpec(
        id=PolicyCapabilityId.CREDENTIALS,
        label="Credentials",
        description="Manage encrypted credentials.",
        tool_prefixes=("credentials.",),
    ),
    PolicyCapabilityId.SUBAGENTS: PolicyCapabilitySpec(
        id=PolicyCapabilityId.SUBAGENTS,
        label="Subagents",
        description="Run/wait/result for subagent tasks.",
        tool_prefixes=("subagent.",),
    ),
    PolicyCapabilityId.AUTOMATION: PolicyCapabilitySpec(
        id=PolicyCapabilityId.AUTOMATION,
        label="Automations",
        description="Create and manage cron/webhook automations.",
        tool_prefixes=("automation.",),
    ),
    PolicyCapabilityId.HTTP: PolicyCapabilitySpec(
        id=PolicyCapabilityId.HTTP,
        label="HTTP requests",
        description="Outbound HTTP requests.",
        tool_names=("http.request",),
    ),
    PolicyCapabilityId.WEB: PolicyCapabilitySpec(
        id=PolicyCapabilityId.WEB,
        label="Web search/fetch",
        description="Search and fetch web pages (Brave + readable fetch).",
        tool_prefixes=("web.",),
    ),
    PolicyCapabilityId.BROWSER: PolicyCapabilitySpec(
        id=PolicyCapabilityId.BROWSER,
        label="Browser control",
        description="Automate browser actions through Playwright runtime.",
        tool_names=("browser.control",),
    ),
    PolicyCapabilityId.SKILLS: PolicyCapabilitySpec(
        id=PolicyCapabilityId.SKILLS,
        label="Skills management",
        description="Manage profile skills and marketplace installs.",
        tool_prefixes=("skill.profile.", "skill.marketplace."),
    ),
    PolicyCapabilityId.APPS: PolicyCapabilitySpec(
        id=PolicyCapabilityId.APPS,
        label="App integrations",
        description="List and execute app integrations via app runtime.",
        tool_names=("app.list", "app.run"),
    ),
    PolicyCapabilityId.MCP: PolicyCapabilitySpec(
        id=PolicyCapabilityId.MCP,
        label="MCP",
        description="Manage profile MCP configs and use runtime-accessible MCP tools from configured remote servers.",
        tool_prefixes=("mcp.",),
    ),
    # Legacy aliases kept for backwards compatibility in non-interactive flags.
    PolicyCapabilityId.EMAIL: PolicyCapabilitySpec(
        id=PolicyCapabilityId.EMAIL,
        label="Email (legacy alias)",
        description="Legacy alias mapped to app integrations capability.",
        tool_names=("app.run", "app.list"),
    ),
    PolicyCapabilityId.TELEGRAM: PolicyCapabilitySpec(
        id=PolicyCapabilityId.TELEGRAM,
        label="Telegram (legacy alias)",
        description="Legacy alias mapped to app integrations capability.",
        tool_names=("app.run", "app.list"),
    ),
    PolicyCapabilityId.DEBUG: PolicyCapabilitySpec(
        id=PolicyCapabilityId.DEBUG,
        label="Debug",
        description="Debug-only tools for diagnostics.",
        tool_names=("debug.echo",),
    ),
}

PRESETS: dict[PolicyPresetLevel, PolicyPresetSpec] = {
    PolicyPresetLevel.SIMPLE: PolicyPresetSpec(
        level=PolicyPresetLevel.SIMPLE,
        default_capabilities=tuple(item for item in CAPABILITY_ORDER if item != PolicyCapabilityId.DEBUG),
        max_iterations_main=DEFAULT_LLM_MAX_ITERATIONS,
        max_iterations_subagent=DEFAULT_LLM_MAX_ITERATIONS,
    ),
    PolicyPresetLevel.MEDIUM: PolicyPresetSpec(
        level=PolicyPresetLevel.MEDIUM,
        default_capabilities=(
            PolicyCapabilityId.MEMORY,
            PolicyCapabilityId.CREDENTIALS,
            PolicyCapabilityId.SUBAGENTS,
            PolicyCapabilityId.AUTOMATION,
            PolicyCapabilityId.HTTP,
            PolicyCapabilityId.WEB,
            PolicyCapabilityId.SKILLS,
            PolicyCapabilityId.APPS,
            PolicyCapabilityId.MCP,
        ),
        max_iterations_main=DEFAULT_LLM_MAX_ITERATIONS,
        max_iterations_subagent=DEFAULT_LLM_MAX_ITERATIONS,
    ),
    PolicyPresetLevel.STRICT: PolicyPresetSpec(
        level=PolicyPresetLevel.STRICT,
        default_capabilities=(
            PolicyCapabilityId.MEMORY,
            PolicyCapabilityId.CREDENTIALS,
            PolicyCapabilityId.HTTP,
        ),
        max_iterations_main=DEFAULT_LLM_MAX_ITERATIONS,
        max_iterations_subagent=DEFAULT_LLM_MAX_ITERATIONS,
    ),
}


def list_capability_specs() -> tuple[PolicyCapabilitySpec, ...]:
    """Return capability specs in stable UX order."""

    return tuple(CAPABILITIES[item] for item in CAPABILITY_ORDER)


def list_preset_levels() -> tuple[PolicyPresetLevel, ...]:
    """Return supported policy preset levels."""

    return tuple(PRESETS.keys())


def get_preset(level: PolicyPresetLevel) -> PolicyPresetSpec:
    """Resolve one preset by enum level."""

    return PRESETS[level]
