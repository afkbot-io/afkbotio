"""Map setup/profile wizard intent answers to canonical policy fields."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.wizard.profile_intent_catalog import quick_safe_profile_intent_defaults

DEFAULT_RESTRICTED_SHELL_COMMANDS: tuple[str, ...] = (
    "ls",
    "cat",
    "pwd",
    "grep",
    "rg",
    "sed",
    "head",
    "tail",
)


@dataclass(frozen=True, slots=True)
class ProfileIntentSelection:
    """One intent-first wizard answer set."""

    depth: str
    work_contexts: tuple[str, ...]
    actions: tuple[str, ...]
    isolation: str
    confirmation: str
    network: str
    network_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileIntentPolicy:
    """Canonical policy fields derived from intent answers."""

    enabled: bool
    preset: str
    capabilities: tuple[str, ...]
    file_access_mode: str
    workspace_scope_mode: str
    shell_sandbox_mode: str
    shell_allowed_commands: tuple[str, ...]
    network_mode: str


def quick_safe_profile_intent_selection() -> ProfileIntentSelection:
    """Return the quick sandbox intent selection used by recommended setup."""

    defaults = quick_safe_profile_intent_defaults()
    work_contexts = defaults["work_contexts"]
    actions = defaults["actions"]
    if not isinstance(work_contexts, tuple) or not isinstance(actions, tuple):
        raise TypeError("quick sandbox intent defaults must use tuple values")
    return ProfileIntentSelection(
        depth=str(defaults["depth"]),
        work_contexts=tuple(str(item) for item in work_contexts),
        actions=tuple(str(item) for item in actions),
        isolation=str(defaults["isolation"]),
        confirmation=str(defaults["confirmation"]),
        network=str(defaults["network"]),
        network_allowlist=(),
    )


def map_profile_intent_to_policy(selection: ProfileIntentSelection) -> ProfileIntentPolicy:
    """Map product-level wizard choices into the existing policy model."""

    depth = selection.depth.strip().lower()
    actions = tuple(
        dict.fromkeys(item.strip().lower() for item in selection.actions if item.strip())
    )
    action_set = set(actions)
    isolation = selection.isolation.strip().lower()
    if depth == "quick":
        return ProfileIntentPolicy(
            enabled=True,
            preset="simple",
            capabilities=_ordered_capabilities(
                [
                    "files",
                    "shell",
                    "memory",
                    "credentials",
                    "subagents",
                    "automation",
                    "taskflow",
                    "http",
                    "web",
                    "browser",
                    "skills",
                    "apps",
                    "mcp",
                ]
            ),
            file_access_mode="read_write",
            workspace_scope_mode="profile_only",
            shell_sandbox_mode="required",
            shell_allowed_commands=(),
            network_mode="unrestricted",
        )
    if isolation not in {
        "no_files",
        "profile_only",
        "project_read",
        "project_write",
        "profile_shell",
        "project_shell",
        "danger_full_system",
    }:
        isolation = "no_files"

    capabilities: list[str] = []
    _add(capabilities, "memory")
    if "taskflow" in action_set:
        _add(capabilities, "taskflow")
    if "automation" in action_set:
        _add(capabilities, "automation")
    if action_set & {"project_read", "project_write", "sandbox_write", "shell_allowlist"}:
        _add(capabilities, "files")
    if "internet_docs" in action_set:
        _add(capabilities, "http")
        _add(capabilities, "web")
    if "browser" in action_set:
        _add(capabilities, "http")
        _add(capabilities, "web")
        _add(capabilities, "browser")
    if "external_services" in action_set:
        _add(capabilities, "http")
    if depth == "expert":
        if "subagents" in action_set:
            _add(capabilities, "subagents")
        if "credentials" in action_set:
            _add(capabilities, "credentials")
        if "afkbot_admin" in action_set:
            for capability in (
                "credentials",
                "subagents",
                "automation",
                "taskflow",
                "http",
                "web",
                "browser",
                "skills",
                "apps",
                "mcp",
            ):
                _add(capabilities, capability)
            if isolation == "danger_full_system":
                _add(capabilities, "files")
                _add(capabilities, "shell")

    file_access_mode = "none"
    workspace_scope_mode = "profile_only"
    shell_sandbox_mode = "disabled"
    shell_allowed_commands: tuple[str, ...] = ()

    if isolation == "no_files":
        capabilities = [item for item in capabilities if item not in {"files", "shell"}]
    elif isolation == "profile_only":
        _add(capabilities, "files")
        file_access_mode = "read_write" if "sandbox_write" in action_set else "read_only"
    elif isolation == "project_read":
        _add(capabilities, "files")
        file_access_mode = "read_only"
        workspace_scope_mode = "project_only"
    elif isolation == "project_write":
        _add(capabilities, "files")
        file_access_mode = "read_write"
        workspace_scope_mode = "project_only"
    elif isolation == "profile_shell":
        _add(capabilities, "files")
        _add(capabilities, "shell")
        file_access_mode = "read_write"
        shell_sandbox_mode = "required"
        shell_allowed_commands = DEFAULT_RESTRICTED_SHELL_COMMANDS
    elif isolation == "project_shell":
        _add(capabilities, "files")
        _add(capabilities, "shell")
        file_access_mode = "read_write"
        workspace_scope_mode = "project_only"
        shell_sandbox_mode = "required"
        shell_allowed_commands = DEFAULT_RESTRICTED_SHELL_COMMANDS
    elif isolation == "danger_full_system":
        _add(capabilities, "files")
        file_access_mode = "read_write"
        workspace_scope_mode = "full_system"
        if "shell_allowlist" in action_set or "afkbot_admin" in action_set:
            _add(capabilities, "shell")
            shell_sandbox_mode = "disabled"

    if "shell_allowlist" in action_set and isolation not in {
        "profile_shell",
        "project_shell",
        "danger_full_system",
    }:
        capabilities = [item for item in capabilities if item != "shell"]

    network_mode = selection.network.strip().lower()
    if network_mode not in {"deny_all", "recommended", "custom", "unrestricted"}:
        network_mode = "recommended"
    if network_mode == "deny_all":
        capabilities = [
            item for item in capabilities if item not in {"http", "web", "browser", "apps"}
        ]

    return ProfileIntentPolicy(
        enabled=True,
        preset=_preset_for_confirmation(selection.confirmation),
        capabilities=_ordered_capabilities(capabilities),
        file_access_mode=file_access_mode,
        workspace_scope_mode=workspace_scope_mode,
        shell_sandbox_mode=shell_sandbox_mode,
        shell_allowed_commands=shell_allowed_commands,
        network_mode=network_mode,
    )


def profile_intent_metadata_payload(selection: ProfileIntentSelection | None) -> dict[str, object]:
    """Return sanitized V2 wizard metadata for runtime/setup persistence."""

    if selection is None:
        return {
            "wizard_setup_depth": "legacy",
            "wizard_work_contexts": (),
            "wizard_actions": (),
            "wizard_isolation": "",
            "wizard_confirmation": "",
            "wizard_network": "",
            "wizard_network_allowlist": (),
        }
    return {
        "wizard_setup_depth": selection.depth,
        "wizard_work_contexts": selection.work_contexts,
        "wizard_actions": selection.actions,
        "wizard_isolation": selection.isolation,
        "wizard_confirmation": selection.confirmation,
        "wizard_network": selection.network,
        "wizard_network_allowlist": selection.network_allowlist,
    }


def profile_intent_selection_from_defaults(
    defaults: dict[str, str],
) -> ProfileIntentSelection | None:
    """Load stored V2 wizard metadata defaults when present and complete."""

    depth = str(defaults.get("AFKBOT_WIZARD_SETUP_DEPTH", "")).strip().lower()
    isolation = str(defaults.get("AFKBOT_WIZARD_ISOLATION", "")).strip().lower()
    confirmation = str(defaults.get("AFKBOT_WIZARD_CONFIRMATION", "")).strip().lower()
    network = str(defaults.get("AFKBOT_WIZARD_NETWORK", "")).strip().lower()
    if not depth or depth == "legacy":
        return None
    return ProfileIntentSelection(
        depth=depth,
        work_contexts=_csv(defaults.get("AFKBOT_WIZARD_WORK_CONTEXTS", "")),
        actions=_csv(defaults.get("AFKBOT_WIZARD_ACTIONS", "")),
        isolation=isolation or "no_files",
        confirmation=confirmation or "balanced",
        network=network or "recommended",
        network_allowlist=_csv(defaults.get("AFKBOT_WIZARD_NETWORK_ALLOWLIST", "")),
    )


def _preset_for_confirmation(confirmation: str) -> str:
    normalized = confirmation.strip().lower()
    if normalized == "fast":
        return "simple"
    if normalized == "strict":
        return "strict"
    return "medium"


def _csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(dict.fromkeys(item.strip().lower() for item in raw.split(",") if item.strip()))


def _ordered_capabilities(capabilities: list[str]) -> tuple[str, ...]:
    order = (
        "files",
        "shell",
        "memory",
        "credentials",
        "subagents",
        "automation",
        "taskflow",
        "http",
        "web",
        "browser",
        "skills",
        "apps",
        "mcp",
    )
    normalized = tuple(dict.fromkeys(capabilities))
    return tuple(item for item in order if item in normalized)


def _add(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
