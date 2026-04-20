"""PartyFlow webhook channel update-command tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing import get_channel_binding_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_partyflow_update_preserves_unspecified_fields_and_updates_keywords(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow update should only mutate explicit fields and preserve the rest."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.partyflow.ru",),
        )
    )

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-partyflow",
            "--profile",
            "default",
            "--credential-profile",
            "ops-partyflow",
            "--trigger-mode",
            "mention",
            "--reply-mode",
            "same_conversation",
            "--tool-profile",
            "chat_minimal",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "update",
            "ops-partyflow",
            "--trigger-mode",
            "keywords",
            "--trigger-keywords",
            "billing, urgent",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2200",
            "--yes",
        ],
    )

    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-partyflow"]).stdout
    assert "- trigger_mode: keywords" in shown
    assert "- trigger_keywords: billing, urgent" in shown
    assert "- reply_mode: same_conversation" in shown
    assert "- tool_profile: chat_minimal" in shown
    assert "- ingress_batch.enabled: True" in shown
    assert "- ingress_batch.debounce_ms: 2200" in shown


def test_channel_partyflow_update_binding_sync_preserves_existing_binding_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Binding sync on PartyFlow update should preserve session policy, priority, and overlay when omitted."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.partyflow.ru",),
        )
    )

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-partyflow",
            "--profile",
            "default",
            "--credential-profile",
            "ops-partyflow",
            "--binding",
            "--session-policy",
            "per-user-in-group",
            "--priority",
            "7",
            "--prompt-overlay",
            "keep partyflow overlay",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "update",
            "ops-partyflow",
            "--binding",
            "--trigger-mode",
            "all",
            "--yes",
        ],
    )
    assert updated.exit_code == 0

    binding = asyncio.run(get_channel_binding_service(settings).get(binding_id="ops-partyflow"))
    assert binding.session_policy == "per-user-in-group"
    assert binding.priority == 7
    assert binding.prompt_overlay == "keep partyflow overlay"
