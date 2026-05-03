"""Wizard plan catalog tests."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.wizard.channel_catalog import (
    channel_plan,
    channel_scenario,
    channel_tool_profile_label,
    list_channel_scenarios,
)
from afkbot.services.wizard.profile_catalog import (
    infer_profile_scenario_id,
    profile_scenario,
    setup_profile_plan,
)
from afkbot.services.wizard.inventory import all_wizard_plans, serialize_wizard_plan
from afkbot.services.wizard.preview import (
    build_channel_preview,
    build_channel_surface_preview,
    build_profile_configuration_preview,
    build_profile_preview,
)


def _question_ids(plan_id: str) -> set[str]:
    if plan_id == "setup_profile":
        plan = setup_profile_plan()
    else:
        plan = channel_plan(plan_id)
    return {question.id for question in plan.questions}


def test_setup_profile_plan_contains_current_security_branches() -> None:
    """Setup/profile wizard inventory should expose the current security decision tree."""

    ids = _question_ids("setup_profile")

    assert {
        "security_ack",
        "ai_provider",
        "chat_model",
        "reasoning_effort",
        "provider_credentials",
        "security_setup_mode",
        "setup_depth",
        "work_contexts",
        "actions",
        "isolation",
        "confirmation",
        "intent_network",
        "security_enforcement",
        "confirmation_mode",
        "profile_scenario",
        "capabilities",
        "file_access",
        "workspace_scope",
        "shell_sandbox",
        "shell_allowed_commands",
        "network_access",
        "update_prompts",
    }.issubset(ids)


def test_channel_plans_contain_shared_and_transport_specific_branches() -> None:
    """Channel wizard inventory should make shared and transport-only branches explicit."""

    telegram = _question_ids("telegram")
    telethon = _question_ids("telethon")
    partyflow = _question_ids("partyflow")

    for ids in (telegram, telethon, partyflow):
        assert {
            "channel_scenario",
            "channel_id",
            "profile",
            "enabled",
            "channel_tool_profile",
            "routing_binding",
            "session_policy",
            "private_access",
            "group_access",
            "outbound_send_targets",
            "ingress_batch",
        }.issubset(ids)

    assert "telegram_group_trigger" in telegram
    assert "telethon_reply_mode" in telethon
    assert "telethon_watcher_digest" in telethon
    assert "partyflow_ingress_mode" in partyflow
    assert "partyflow_include_context" in partyflow
    assert "partyflow_signing_secret" in partyflow


def test_channel_scenario_catalog_covers_each_transport_mode() -> None:
    """High-level channel scenarios should cover common trigger and delivery variants."""

    telegram_ids = {scenario.id for scenario in list_channel_scenarios(transport="telegram")}
    telethon_ids = {scenario.id for scenario in list_channel_scenarios(transport="telethon")}
    partyflow_ids = {scenario.id for scenario in list_channel_scenarios(transport="partyflow")}

    assert {
        "telegram_private_dm",
        "telegram_group_mention",
        "telegram_group_all_messages",
        "telegram_trusted_admin",
    }.issubset(telegram_ids)
    assert {
        "telethon_private_reply",
        "telethon_group_command",
        "telethon_watcher_digest",
        "telethon_trusted_admin",
    }.issubset(telethon_ids)
    assert {
        "partyflow_private_mention",
        "partyflow_webhook_keywords",
        "partyflow_group_all_messages",
        "partyflow_trusted_admin",
    }.issubset(partyflow_ids)


def test_wizard_plans_expose_branch_graphs() -> None:
    """Wizard plans should model branch visibility instead of only free-text prompts."""

    setup = setup_profile_plan()
    partyflow = channel_plan("partyflow")

    assert {branch.id for branch in setup.branches} >= {
        "guided_security",
        "expert_security",
        "files_enabled",
        "shell_enabled",
    }
    assert {branch.id for branch in partyflow.branches} >= {
        "scenario_defaults",
        "trusted_admin",
        "routing_enabled",
        "access_allowlists",
        "ingress_batch",
    }
    trusted_admin = next(branch for branch in partyflow.branches if branch.id == "trusted_admin")
    assert "trusted_admin" in trusted_admin.condition
    assert "channel_scenario" == partyflow.questions[0].id


def test_profile_scenario_defaults_are_safe_and_useful() -> None:
    """Scenario presets should encode product-level intent without broad defaults."""

    taskflow = profile_scenario("taskflow_channel")
    sandbox_shell = profile_scenario("sandbox_shell")
    trusted = profile_scenario("trusted_admin")

    assert taskflow.capabilities == ("memory", "taskflow")
    assert taskflow.file_access_mode == "none"
    assert taskflow.shell_sandbox_mode == "disabled"

    assert sandbox_shell.workspace_scope_mode == "profile_only"
    assert sandbox_shell.file_access_mode == "read_write"
    assert sandbox_shell.shell_sandbox_mode == "required"
    assert sandbox_shell.default_shell_allowed_commands

    assert trusted.workspace_scope_mode == "full_system"
    assert "shell" in trusted.capabilities


def test_infer_profile_scenario_keeps_old_or_custom_configs_compatible() -> None:
    """Legacy configs without wizard metadata should infer a conservative scenario id."""

    assert (
        infer_profile_scenario_id(
            capabilities=("memory", "taskflow"),
            file_access_mode="none",
            workspace_scope_mode="profile_only",
            shell_sandbox_mode="disabled",
            shell_allowed_commands=(),
            network_mode="recommended",
            network_allowlist=(),
        )
        == "taskflow_channel"
    )
    assert (
        infer_profile_scenario_id(
            capabilities=("files", "shell", "memory"),
            file_access_mode="read_write",
            workspace_scope_mode="custom",
            shell_sandbox_mode="best_effort",
            shell_allowed_commands=("ls",),
            network_mode="custom",
            network_allowlist=("example.com",),
        )
        == "custom"
    )


def test_infer_profile_scenario_rejects_custom_network_or_shell_drift() -> None:
    """Wizard metadata should not mislabel profiles that only look partly like a scenario."""

    assert (
        infer_profile_scenario_id(
            capabilities=("files", "shell", "memory"),
            file_access_mode="read_write",
            workspace_scope_mode="profile_only",
            shell_sandbox_mode="required",
            shell_allowed_commands=("ls",),
            network_mode="recommended",
            network_allowlist=(),
        )
        == "custom"
    )
    assert (
        infer_profile_scenario_id(
            capabilities=("memory", "taskflow"),
            file_access_mode="none",
            workspace_scope_mode="profile_only",
            shell_sandbox_mode="disabled",
            shell_allowed_commands=(),
            network_mode="custom",
            network_allowlist=("api.partyflow.ru",),
        )
        == "custom"
    )


def test_wizard_preview_explains_profile_and_channel_boundaries() -> None:
    """Preview should explain profile ceiling versus channel surface in Russian."""

    profile_preview = build_profile_preview(
        scenario=profile_scenario("sandbox_shell"),
        allowed_directories=("/tmp/afk/profiles/default",),
        network_allowlist=("api.telegram.org",),
        lang=PromptLanguage.RU,
    )
    channel_preview = build_channel_preview(
        scenario=channel_scenario("partyflow_private_mention"),
        lang=PromptLanguage.RU,
    )

    profile_text = "\n".join(profile_preview.lines)
    channel_text = "\n".join(channel_preview.lines)

    assert "Потолок профиля" in profile_text
    assert "только директории" in profile_text
    assert "Shell sandbox: required" in profile_text
    assert "Поверхность канала" in channel_text
    assert "PartyFlow" in channel_text
    assert "без общего app.run, shell или файлов" in channel_text


def test_effective_preview_includes_credentials_network_and_warnings() -> None:
    """CLI-facing preview builders should cover credentials, network, and warning branches."""

    profile_preview = build_profile_configuration_preview(
        scenario_id="custom",
        capabilities=("files", "shell"),
        file_access_mode="read_write",
        workspace_scope_mode="full_system",
        allowed_directories=("/",),
        shell_sandbox_mode="best_effort",
        shell_allowed_commands=("ls",),
        network_mode="custom",
        network_allowlist=("api.partyflow.ru",),
        credential_status=("llm_api_key_configured",),
        lang=PromptLanguage.EN,
    )
    channel_preview = build_channel_surface_preview(
        transport="partyflow",
        scenario_id="partyflow_private_mention",
        tool_profile="messaging_safe",
        trigger_mode="mention",
        reply_mode="same_conversation",
        private_policy="disabled",
        group_policy="open",
        current_channel_tools=("channel.history.list",),
        credential_status=("bot_token_configured", "signing_secret_optional"),
        lang=PromptLanguage.EN,
    )

    profile_text = "\n".join(profile_preview.lines)
    channel_text = "\n".join(channel_preview.lines)

    assert "credentials: llm_api_key_configured" in profile_text
    assert "network: api.partyflow.ru" in profile_text
    assert "best_effort is not hard isolation" in profile_text
    assert "full_system exposes all local files" in profile_text
    assert "credentials: bot_token_configured, signing_secret_optional" in channel_text
    assert "channel-owned tools are scoped to the active endpoint" in channel_text


def test_channel_tool_profile_labels_are_centralized_and_localized() -> None:
    """CLI labels should come from one catalog and remain localized."""

    assert channel_tool_profile_label("taskflow_operator", lang=PromptLanguage.EN) == (
        "Task operator - create and update tasks from the channel, no terminal or files"
    )
    assert channel_tool_profile_label("chat_minimal", lang=PromptLanguage.RU) == (
        "Минимальный чат - ответы и история текущего канала, без общих инструментов"
    )


def test_wizard_inventory_is_serializable_and_stable() -> None:
    """Inventory should be exportable for diagnostics and snapshot-style coverage."""

    payloads = [serialize_wizard_plan(plan) for plan in all_wizard_plans()]

    assert [item["id"] for item in payloads] == [
        "setup_profile",
        "channel_telegram",
        "channel_telethon",
        "channel_partyflow",
    ]
    assert payloads[0]["questions"][0]["id"] == "security_ack"
    assert payloads[1]["questions"][0]["id"] == "channel_scenario"
    assert payloads[1]["branches"]
    assert payloads[-1]["questions"][-1]["id"] == "partyflow_signing_secret"
