"""Tests for the intent-first setup/profile wizard catalog."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.wizard.profile_intent_catalog import (
    profile_intent_action_choices_for_contexts,
    list_profile_intent_actions,
    list_profile_intent_confirmations,
    list_profile_intent_depths,
    list_profile_intent_isolations,
    list_profile_intent_work_contexts,
    profile_intent_default_actions,
)
from afkbot.services.wizard.profile_intent_mapper import (
    ProfileIntentSelection,
    map_profile_intent_to_policy,
)


def _labels(values: tuple[object, ...]) -> str:
    return "\n".join(
        item.label(lang=PromptLanguage.RU)  # type: ignore[attr-defined]
        for item in values
    )


def test_intent_wizard_ru_labels_are_product_language_not_internal_jargon() -> None:
    """Primary setup choices should use understandable Russian product language."""

    text = "\n".join(
        (
            _labels(list_profile_intent_depths()),
            _labels(list_profile_intent_work_contexts()),
            _labels(list_profile_intent_actions(include_expert=False)),
            _labels(list_profile_intent_isolations(include_dangerous=False)),
            _labels(list_profile_intent_confirmations()),
        )
    )

    assert "Каналы и чаты" in text
    assert "Автоматизации" in text
    assert "Создавать и обновлять задачи" in text
    assert "Только личная папка профиля" in text
    assert "Вручную" in text
    for forbidden in (
        "Task Flow from a channel",
        "Trusted admin",
        "Администрирование AFKBOT",
        "simple -",
        "medium -",
        "strict -",
        "full_system",
    ):
        assert forbidden not in text


def test_default_actions_follow_selected_work_contexts() -> None:
    """Guided wizard defaults should be useful without enabling risky tool categories."""

    channel_defaults = profile_intent_default_actions(("channels",))
    project_defaults = profile_intent_default_actions(("project",))
    automation_defaults = profile_intent_default_actions(("automations",))

    assert {"reply", "channel_history", "memory"}.issubset(channel_defaults)
    assert "project_read" in project_defaults
    assert "automation" in automation_defaults
    assert "shell_allowlist" not in channel_defaults
    assert "credentials" not in automation_defaults


def test_action_choices_are_filtered_by_selected_work_contexts() -> None:
    """Guided Q3 should not show project/shell actions for a channel-only profile."""

    channel_actions = {
        choice.id
        for choice in profile_intent_action_choices_for_contexts(("channels",), include_expert=False)
    }
    project_actions = {
        choice.id
        for choice in profile_intent_action_choices_for_contexts(("project",), include_expert=False)
    }

    assert {"reply", "channel_history", "channel_send", "taskflow", "memory"}.issubset(channel_actions)
    assert "project_read" not in channel_actions
    assert "sandbox_write" not in channel_actions
    assert "shell_allowlist" not in channel_actions
    assert {"project_read", "project_write", "external_services"}.issubset(project_actions)


def test_intent_mapper_keeps_channel_task_profile_without_files_or_shell() -> None:
    """A channel/task operator profile should not get generic file or shell tools."""

    resolved = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="guided",
            work_contexts=("channels",),
            actions=("reply", "channel_history", "taskflow", "memory"),
            isolation="no_files",
            confirmation="balanced",
            network="recommended",
        )
    )

    assert resolved.preset == "medium"
    assert resolved.capabilities == ("memory", "taskflow")
    assert resolved.file_access_mode == "none"
    assert resolved.workspace_scope_mode == "profile_only"
    assert resolved.shell_sandbox_mode == "disabled"
    assert resolved.shell_allowed_commands == ()
    assert resolved.network_mode == "recommended"


def test_intent_mapper_deny_all_removes_external_service_capabilities() -> None:
    """Network-deny profiles must not retain HTTP/browser/app integration surfaces."""

    resolved = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="guided",
            work_contexts=("automations",),
            actions=("memory", "automation", "external_services", "internet_docs", "browser"),
            isolation="no_files",
            confirmation="balanced",
            network="deny_all",
        )
    )

    assert "http" not in resolved.capabilities
    assert "web" not in resolved.capabilities
    assert "browser" not in resolved.capabilities
    assert "apps" not in resolved.capabilities


def test_intent_mapper_does_not_turn_project_readonly_shell_into_write_access() -> None:
    """Read-only isolation should not silently become write access just because shell was selected."""

    resolved = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="guided",
            work_contexts=("project",),
            actions=("memory", "project_read", "shell_allowlist"),
            isolation="project_read",
            confirmation="balanced",
            network="deny_all",
        )
    )

    assert resolved.file_access_mode == "read_only"
    assert resolved.workspace_scope_mode == "project_only"
    assert "shell" not in resolved.capabilities
    assert resolved.shell_sandbox_mode == "disabled"


def test_intent_mapper_requires_sandbox_for_allowlisted_shell() -> None:
    """Allowlisted shell should imply file scope to the profile sandbox and fail-closed shell mode."""

    resolved = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="guided",
            work_contexts=("sandbox",),
            actions=("memory", "sandbox_write", "shell_allowlist"),
            isolation="profile_shell",
            confirmation="strict",
            network="deny_all",
        )
    )

    assert resolved.preset == "strict"
    assert resolved.capabilities == ("files", "shell", "memory")
    assert resolved.file_access_mode == "read_write"
    assert resolved.workspace_scope_mode == "profile_only"
    assert resolved.shell_sandbox_mode == "required"
    assert "rg" in resolved.shell_allowed_commands
    assert resolved.network_mode == "deny_all"


def test_intent_mapper_keeps_dangerous_admin_behind_expert_selection() -> None:
    """Full-system admin access should require the explicit expert admin action and dangerous isolation."""

    normal = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="guided",
            work_contexts=("channels", "automations"),
            actions=("reply", "automation", "taskflow", "memory"),
            isolation="no_files",
            confirmation="balanced",
            network="recommended",
        )
    )
    expert = map_profile_intent_to_policy(
        ProfileIntentSelection(
            depth="expert",
            work_contexts=("expert",),
            actions=("credentials", "afkbot_admin", "shell_allowlist"),
            isolation="danger_full_system",
            confirmation="strict",
            network="unrestricted",
        )
    )

    assert "credentials" not in normal.capabilities
    assert normal.workspace_scope_mode != "full_system"
    assert {"credentials", "apps", "mcp", "shell"}.issubset(expert.capabilities)
    assert expert.workspace_scope_mode == "full_system"
