"""Policy-specific input resolvers for setup CLI."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

import typer

from afkbot.cli.presentation.setup_prompts import (
    PromptLanguage,
    prompt_policy_capabilities,
    prompt_policy_enabled,
    prompt_policy_file_access_mode,
    prompt_policy_network_mode,
    prompt_policy_preset,
    prompt_policy_setup_mode,
    prompt_policy_workspace_scope_mode,
)
from afkbot.services.setup.contracts import (
    NETWORK_POLICY_RECOMMENDED_HOSTS,
    PolicyFileAccessMode,
    PolicyNetworkMode,
    PolicySetupMode,
    WILDCARD_NETWORK_HOST,
)
from afkbot.services.setup.defaults import read_bool_default
from afkbot.services.policy import (
    PolicyPresetLevel,
    default_capabilities_for_preset,
    normalize_workspace_scope_mode,
    parse_capability_ids,
    parse_preset_level,
)


def confirmation_mode_for_preset(preset: str) -> str:
    """Map policy preset to approval behavior saved in runtime config."""

    normalized = parse_preset_level(preset).value
    if normalized == PolicyPresetLevel.SIMPLE.value:
        return "none"
    if normalized == PolicyPresetLevel.STRICT.value:
        return "critical_mutations"
    return "destructive_files"


def has_explicit_policy_overrides(
    *,
    policy_enabled: bool | None,
    policy_preset: str | None,
    policy_capability: tuple[str, ...],
    policy_file_access_mode: str | None,
    policy_workspace_scope: str | None,
    policy_network_host: tuple[str, ...],
) -> bool:
    """Return whether setup flags force the custom policy path."""

    return (
        policy_enabled is not None
        or policy_preset is not None
        or bool(policy_capability)
        or policy_file_access_mode is not None
        or policy_workspace_scope is not None
        or bool(policy_network_host)
    )


def default_policy_enabled_for_preset(*, defaults: dict[str, str], preset: str) -> bool:
    """Resolve default policy-enabled value for one preset."""

    persisted_raw = defaults.get("AFKBOT_POLICY_ENABLED")
    if persisted_raw not in {None, ""}:
        return read_bool_default(persisted_raw, True)
    _ = parse_preset_level(preset)
    return True


def resolve_policy_setup_mode(
    *,
    interactive: bool,
    defaults: dict[str, str],
    explicit_policy_overrides: bool,
    lang: PromptLanguage,
) -> str:
    """Resolve whether setup uses recommended or custom security setup path."""

    if explicit_policy_overrides:
        return PolicySetupMode.CUSTOM.value
    default = str(
        defaults.get("AFKBOT_POLICY_SETUP_MODE", PolicySetupMode.RECOMMENDED.value)
    ).strip().lower()
    if default not in {item.value for item in PolicySetupMode}:
        default = PolicySetupMode.RECOMMENDED.value
    if interactive:
        return prompt_policy_setup_mode(default=default, lang=lang).strip().lower()
    return default


def resolve_policy_enabled(
    *,
    value: bool | None,
    interactive: bool,
    default: bool,
    lang: PromptLanguage,
) -> bool:
    """Resolve runtime policy enabled flag."""

    if value is not None:
        return value
    if interactive:
        return prompt_policy_enabled(default=default, lang=lang)
    return default


def resolve_policy_preset(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve setup policy preset string."""

    if value is not None:
        preset = value.strip().lower()
    elif interactive:
        preset = prompt_policy_preset(default=default, lang=lang).strip().lower()
    else:
        preset = str(default).strip().lower()
    try:
        return parse_preset_level(preset).value
    except ValueError as exc:
        raise typer.BadParameter(
            "policy preset must be one of: simple, medium, strict (aliases: light, hard)"
        ) from exc


def resolve_policy_capabilities(
    *,
    value: tuple[str, ...],
    interactive: bool,
    preset: str,
    defaults: dict[str, str],
    lang: PromptLanguage,
) -> tuple[str, ...]:
    """Resolve capability ids for setup policy selection."""

    if value:
        parsed = parse_capability_ids(value)
        return tuple(item.value for item in parsed)

    default_from_env = tuple(
        item.strip().lower()
        for item in defaults.get("AFKBOT_POLICY_CAPABILITIES", "").split(",")
        if item.strip()
    )
    if interactive:
        selected = prompt_policy_capabilities(
            preset=preset,
            lang=lang,
            exclude_values=("debug",),
            default_values=default_from_env or None,
        )
        parsed = parse_capability_ids(selected)
        return tuple(item.value for item in parsed)
    if default_from_env:
        parsed = parse_capability_ids(default_from_env)
        return tuple(item.value for item in parsed)
    preset_level = parse_preset_level(preset)
    return tuple(item.value for item in default_capabilities_for_preset(preset_level))


def resolve_policy_network_settings(
    *,
    value: tuple[str, ...],
    interactive: bool,
    defaults: dict[str, str],
    capabilities: tuple[str, ...],
    lang: PromptLanguage,
) -> tuple[str, tuple[str, ...]]:
    """Resolve network mode and allowlist tuple for setup/profile policy."""

    if value:
        explicit_hosts = parse_policy_network_hosts(
            raw_values=value,
            source="--policy-network-host",
        )
        if WILDCARD_NETWORK_HOST in explicit_hosts:
            return PolicyNetworkMode.UNRESTRICTED.value, (WILDCARD_NETWORK_HOST,)
        return PolicyNetworkMode.CUSTOM.value, explicit_hosts

    network_mode = resolve_policy_network_mode(
        interactive=interactive,
        defaults=defaults,
        capabilities=capabilities,
        lang=lang,
    )
    if network_mode == PolicyNetworkMode.UNRESTRICTED.value:
        return network_mode, (WILDCARD_NETWORK_HOST,)
    if network_mode == PolicyNetworkMode.DENY_ALL.value:
        return network_mode, ()
    if network_mode == PolicyNetworkMode.CUSTOM.value:
        custom_hosts = parse_policy_network_hosts(
            raw_values=(defaults.get("AFKBOT_POLICY_NETWORK_ALLOWLIST", ""),),
            source="AFKBOT_POLICY_NETWORK_ALLOWLIST",
        )
        return network_mode, custom_hosts
    return network_mode, recommended_policy_network_hosts(capabilities=capabilities)


def resolve_policy_network_mode(
    *,
    interactive: bool,
    defaults: dict[str, str],
    capabilities: tuple[str, ...],
    lang: PromptLanguage,
) -> str:
    """Resolve network policy mode from defaults or interactive choice."""

    default_mode = default_policy_network_mode(defaults=defaults, capabilities=capabilities)
    if not interactive:
        return default_mode
    allow_custom = default_mode == PolicyNetworkMode.CUSTOM.value
    resolved = prompt_policy_network_mode(
        default=default_mode,
        lang=lang,
        allow_custom=allow_custom,
    ).strip().lower()
    if resolved == "custom" and allow_custom:
        return PolicyNetworkMode.CUSTOM.value
    return resolved


def default_policy_network_mode(
    *,
    defaults: dict[str, str],
    capabilities: tuple[str, ...],
) -> str:
    """Resolve default network mode from persisted allowlist/mode values."""

    raw_mode = str(defaults.get("AFKBOT_POLICY_NETWORK_MODE", "")).strip().lower()
    if raw_mode in {item.value for item in PolicyNetworkMode}:
        return raw_mode
    persisted = parse_policy_network_hosts(
        raw_values=(defaults.get("AFKBOT_POLICY_NETWORK_ALLOWLIST", ""),),
        source="AFKBOT_POLICY_NETWORK_ALLOWLIST",
    )
    if WILDCARD_NETWORK_HOST in persisted:
        return PolicyNetworkMode.UNRESTRICTED.value
    if not persisted:
        return PolicyNetworkMode.DENY_ALL.value
    recommended = recommended_policy_network_hosts(capabilities=capabilities)
    if persisted == recommended:
        return PolicyNetworkMode.RECOMMENDED.value
    return PolicyNetworkMode.CUSTOM.value


def resolve_policy_file_access_mode(
    *,
    value: str | None,
    interactive: bool,
    defaults: dict[str, str],
    lang: PromptLanguage,
) -> str:
    """Resolve file-tool access mode used to filter allowed tools."""

    if value is not None:
        normalized = value.strip().lower()
    else:
        normalized = str(
            defaults.get("AFKBOT_POLICY_FILE_ACCESS_MODE", PolicyFileAccessMode.READ_WRITE.value)
        ).strip().lower()
        if interactive:
            normalized = prompt_policy_file_access_mode(default=normalized, lang=lang).strip().lower()
    if normalized not in {item.value for item in PolicyFileAccessMode}:
        raise typer.BadParameter("policy file access mode must be one of: none, read_only, read_write")
    return normalized


def resolve_policy_workspace_scope_mode(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
    allow_custom: bool = False,
) -> str:
    """Resolve high-level workspace scope mode for file/shell tools."""

    if value is not None:
        try:
            return normalize_workspace_scope_mode(value)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    if interactive:
        selected = prompt_policy_workspace_scope_mode(
            default=default,
            lang=lang,
            allow_custom=allow_custom,
        ).strip().lower()
        try:
            return normalize_workspace_scope_mode(selected)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    try:
        return normalize_workspace_scope_mode(default)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def recommended_policy_network_hosts(*, capabilities: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve recommended host allowlist from selected capabilities."""

    hosts: list[str] = []
    seen: set[str] = set()
    for capability in capabilities:
        for host in NETWORK_POLICY_RECOMMENDED_HOSTS.get(capability, ()):
            normalized = host.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            hosts.append(normalized)
    return tuple(hosts)


def parse_policy_network_hosts(*, raw_values: Iterable[str], source: str) -> tuple[str, ...]:
    """Normalize repeated/comma-separated host values into one stable tuple."""

    hosts: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for fragment in str(raw).split(","):
            value = fragment.strip()
            if not value:
                continue
            normalized = normalize_policy_network_host(value=value, source=source)
            if normalized in seen:
                continue
            seen.add(normalized)
            hosts.append(normalized)
    return tuple(hosts)


def normalize_policy_network_host(*, value: str, source: str) -> str:
    """Validate and normalize one allowlisted network host/domain."""

    if value.strip() == WILDCARD_NETWORK_HOST:
        return WILDCARD_NETWORK_HOST
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = parsed.hostname or value
    normalized = host.strip().lower().lstrip("*.").strip(".")
    if not normalized:
        raise typer.BadParameter(f"Invalid network allowlist host in {source}: {value}")
    if is_ipv4_address(normalized):
        return normalized
    if normalized == "localhost":
        return normalized
    if not is_valid_dns_host(normalized):
        raise typer.BadParameter(f"Invalid network allowlist host in {source}: {value}")
    return normalized


def is_ipv4_address(value: str) -> bool:
    """Return whether value is a dotted IPv4 address."""

    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        number = int(part)
        if number < 0 or number > 255:
            return False
    return True


def is_valid_dns_host(value: str) -> bool:
    """Return whether value looks like a valid DNS host label sequence."""

    if len(value) > 253:
        return False
    labels = value.split(".")
    if not labels:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if not label[0].isalnum() or not label[-1].isalnum():
            return False
        if not all(char.isalnum() or char == "-" for char in label):
            return False
    return True
