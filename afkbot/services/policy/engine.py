"""Unified profile policy evaluation engine."""

from __future__ import annotations

from pathlib import Path

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.policy.contracts import PolicyViolationError
from afkbot.services.policy.evaluation_helpers import (
    contains_command_substitution,
    extract_commands,
    extract_hosts,
    extract_path_values,
    host_matches,
    normalize_path,
    parse_string_set,
)
_FIXED_OUTBOUND_HOSTS_BY_TOOL: dict[str, tuple[str, ...]] = {
    "web.search": ("api.search.brave.com",),
}


class PolicyEngine:
    """Evaluate and enforce profile policy fields for runtime actions."""

    def __init__(self, *, root_dir: Path | None = None) -> None:
        self._root_dir = root_dir.resolve() if root_dir is not None else None

    def effective_main_iterations(self, *, policy: ProfilePolicy, runtime_limit: int) -> int:
        """Return effective main-agent iteration limit after policy cap."""

        if not self._is_policy_enabled(policy):
            return max(1, int(runtime_limit))
        loop_limit = max(1, int(runtime_limit))
        policy_limit = max(0, int(policy.max_iterations_main))
        return min(loop_limit, policy_limit)

    def ensure_subagent_run_allowed(self, *, policy: ProfilePolicy) -> None:
        """Validate policy for subagent runtime entrypoint."""

        if not self._is_policy_enabled(policy):
            return
        self.ensure_tool_call_allowed(
            policy=policy,
            tool_name="subagent.run",
            params={},
        )
        if int(policy.max_iterations_subagent) <= 0:
            raise PolicyViolationError(
                reason="Subagent execution is disabled by policy: max_iterations_subagent <= 0"
            )

    def allowed_tool_names(
        self,
        *,
        policy: ProfilePolicy,
        available_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Return tool names visible to planner under allow/deny policy."""

        if not self._is_policy_enabled(policy):
            return tuple(available_names)
        try:
            denied = parse_string_set(
                raw=policy.denied_tools_json,
                field_name="denied_tools_json",
            )
            allowed = parse_string_set(
                raw=policy.allowed_tools_json,
                field_name="allowed_tools_json",
            )
        except PolicyViolationError:
            return ()

        visible: list[str] = []
        for name in available_names:
            if any(_tool_rule_matches(rule=rule, tool_name=name) for rule in denied):
                continue
            if allowed and not any(_tool_rule_matches(rule=rule, tool_name=name) for rule in allowed):
                continue
            visible.append(name)
        return tuple(visible)

    def ensure_tool_call_allowed(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
    ) -> None:
        """Validate one tool invocation against profile policy fields."""

        if not self._is_policy_enabled(policy):
            return
        self._enforce_tool_lists(policy=policy, tool_name=tool_name)
        self._enforce_path_lists(policy=policy, params=params)
        self._enforce_shell_lists(policy=policy, params=params)
        self._enforce_network_allowlist(
            policy=policy,
            tool_name=tool_name,
            params=params,
        )

    @staticmethod
    def _is_policy_enabled(policy: ProfilePolicy) -> bool:
        """Return true when profile policy enforcement is enabled."""

        return bool(getattr(policy, "policy_enabled", True))

    def _enforce_tool_lists(self, *, policy: ProfilePolicy, tool_name: str) -> None:
        denied = parse_string_set(raw=policy.denied_tools_json, field_name="denied_tools_json")
        if any(_tool_rule_matches(rule=rule, tool_name=tool_name) for rule in denied):
            raise PolicyViolationError(reason=f"Tool is denied by policy: {tool_name}")

        allowed = parse_string_set(
            raw=policy.allowed_tools_json,
            field_name="allowed_tools_json",
        )
        if allowed and not any(_tool_rule_matches(rule=rule, tool_name=tool_name) for rule in allowed):
            raise PolicyViolationError(reason=f"Tool is not allowed by policy: {tool_name}")

    def _enforce_path_lists(
        self,
        *,
        policy: ProfilePolicy,
        params: dict[str, object],
    ) -> None:
        allowed_dirs = parse_string_set(
            raw=policy.allowed_directories_json,
            field_name="allowed_directories_json",
        )
        if not allowed_dirs:
            return

        allowed_roots = [self._normalize_path(value) for value in allowed_dirs]
        for candidate in extract_path_values(params):
            normalized = self._normalize_path(candidate)
            if any(normalized.is_relative_to(root) for root in allowed_roots):
                continue
            raise PolicyViolationError(reason=f"Path is not allowed by policy: {candidate}")

    def _enforce_shell_lists(self, *, policy: ProfilePolicy, params: dict[str, object]) -> None:
        commands = extract_commands(params)
        if not commands:
            return

        denied = parse_string_set(
            raw=policy.shell_denied_commands_json,
            field_name="shell_denied_commands_json",
        )
        allowed = parse_string_set(
            raw=policy.shell_allowed_commands_json,
            field_name="shell_allowed_commands_json",
        )
        if (allowed or denied) and contains_command_substitution(params):
            raise PolicyViolationError(reason="Shell command substitution is not allowed by policy")

        for command in commands:
            if command in denied:
                raise PolicyViolationError(reason=f"Shell command is denied by policy: {command}")
        for command in commands:
            if allowed and command not in allowed:
                raise PolicyViolationError(reason=f"Shell command is not allowed by policy: {command}")

    def _enforce_network_allowlist(
        self,
        *,
        policy: ProfilePolicy,
        tool_name: str,
        params: dict[str, object],
    ) -> None:
        hosts = extract_hosts(params)
        fixed_hosts = _FIXED_OUTBOUND_HOSTS_BY_TOOL.get(tool_name, ())
        if fixed_hosts:
            hosts.extend(fixed_hosts)
            hosts = list(dict.fromkeys(hosts))
        if not hosts:
            return

        allowlist = parse_string_set(
            raw=policy.network_allowlist_json,
            field_name="network_allowlist_json",
        )
        if not allowlist:
            raise PolicyViolationError(
                reason="Network access is denied by policy: network_allowlist_json is empty"
            )

        for host in hosts:
            if any(host_matches(host=host, allowed=allowed_host) for allowed_host in allowlist):
                continue
            raise PolicyViolationError(reason=f"Network host is not allowed by policy: {host}")

    def _normalize_path(self, raw: str) -> Path:
        normalized_path = normalize_path(root_dir=self._root_dir, raw=raw)
        return Path(normalized_path)


def _tool_rule_matches(*, rule: str, tool_name: str) -> bool:
    """Return whether one allowed/denied rule matches a concrete tool name."""

    normalized_rule = rule.strip()
    if not normalized_rule:
        return False
    if normalized_rule.endswith("*"):
        return tool_name.startswith(normalized_rule[:-1])
    return tool_name == normalized_rule
