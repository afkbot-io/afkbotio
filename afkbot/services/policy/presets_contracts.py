"""Contracts for policy presets and capability selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PolicyPresetLevel(StrEnum):
    """Supported setup/profile policy preset levels."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    STRICT = "strict"


class PolicyCapabilityId(StrEnum):
    """Stable capability identifiers shown during setup/profile flows."""

    FILES = "files"
    SHELL = "shell"
    MEMORY = "memory"
    CREDENTIALS = "credentials"
    SUBAGENTS = "subagents"
    AUTOMATION = "automation"
    TASKFLOW = "taskflow"
    HTTP = "http"
    WEB = "web"
    BROWSER = "browser"
    SKILLS = "skills"
    APPS = "apps"
    MCP = "mcp"
    EMAIL = "email"
    TELEGRAM = "telegram"
    DEBUG = "debug"


@dataclass(frozen=True, slots=True)
class PolicyCapabilitySpec:
    """Capability-to-tools mapping contract."""

    id: PolicyCapabilityId
    label: str
    description: str
    tool_prefixes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyPresetSpec:
    """Preset defaults and guardrail limits."""

    level: PolicyPresetLevel
    default_capabilities: tuple[PolicyCapabilityId, ...]
    max_iterations_main: int
    max_iterations_subagent: int


@dataclass(frozen=True, slots=True)
class PolicySelection:
    """User policy selection payload before tool resolution."""

    enabled: bool
    preset: PolicyPresetLevel
    capabilities: tuple[PolicyCapabilityId, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    """Deterministic runtime policy derived from a policy selection."""

    enabled: bool
    preset: PolicyPresetLevel
    capabilities: tuple[PolicyCapabilityId, ...]
    allowed_tools: tuple[str, ...]
    max_iterations_main: int
    max_iterations_subagent: int
