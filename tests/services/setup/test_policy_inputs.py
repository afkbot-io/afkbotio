"""Tests for setup policy input resolvers."""

from __future__ import annotations

from pytest import MonkeyPatch

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.setup.policy_inputs import (
    resolve_policy_capabilities,
    resolve_policy_setup_mode,
)
from afkbot.services.setup.profile_resolution import resolve_profile_policy_inputs


def test_resolve_policy_setup_mode_uses_persisted_default_in_interactive(
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive setup-mode prompt should receive persisted default selection."""

    captured: dict[str, str] = {}

    def _fake_prompt(*, default: str, lang: PromptLanguage) -> str:
        captured["default"] = default
        assert lang == PromptLanguage.EN
        return default

    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_setup_mode", _fake_prompt)

    resolved = resolve_policy_setup_mode(
        interactive=True,
        defaults={"AFKBOT_POLICY_SETUP_MODE": "custom"},
        explicit_policy_overrides=False,
        lang=PromptLanguage.EN,
    )

    assert captured["default"] == "custom"
    assert resolved == "custom"


def test_resolve_policy_capabilities_uses_persisted_defaults_in_interactive(
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive capability prompt should preselect persisted capability values."""

    captured: dict[str, tuple[str, ...]] = {}

    def _fake_prompt(
        *,
        preset: str,
        lang: PromptLanguage,
        exclude_values: tuple[str, ...] = (),
        default_values: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        captured["preset"] = (preset,)
        captured["default_values"] = default_values or ()
        assert lang == PromptLanguage.EN
        assert exclude_values == ("debug",)
        return default_values or ()

    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_capabilities", _fake_prompt)

    resolved = resolve_policy_capabilities(
        value=(),
        interactive=True,
        preset="strict",
        defaults={"AFKBOT_POLICY_CAPABILITIES": "files,shell"},
        lang=PromptLanguage.EN,
    )

    assert captured["preset"] == ("strict",)
    assert captured["default_values"] == ("files", "shell")
    assert resolved == ("files", "shell")


def test_resolve_profile_policy_inputs_skips_follow_up_prompts_when_policy_disabled(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive policy flow should stop prompting for detailed policy knobs after disable."""

    def _fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("follow-up policy prompt should not run when enforcement is disabled")

    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_capabilities", _fail)
    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_file_access_mode", _fail)
    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_workspace_scope_mode", _fail)
    monkeypatch.setattr("afkbot.services.setup.policy_inputs.prompt_policy_network_mode", _fail)

    resolved = resolve_profile_policy_inputs(
        interactive=True,
        lang=PromptLanguage.EN,
        root_dir=tmp_path,
        profile_root=tmp_path / "profiles/default",
        defaults={
            "AFKBOT_POLICY_PRESET": "strict",
            "AFKBOT_POLICY_CAPABILITIES": "files,shell,memory",
            "AFKBOT_POLICY_FILE_ACCESS_MODE": "read_write",
            "AFKBOT_POLICY_WORKSPACE_SCOPE": "profile_only",
            "AFKBOT_POLICY_NETWORK_MODE": "recommended",
        },
        policy_enabled_value=False,
        policy_preset_value=None,
        policy_capability_values=(),
        policy_file_access_mode_value=None,
        policy_workspace_scope_value=None,
        policy_allowed_dir_values=(),
        policy_network_host_values=(),
    )

    assert resolved.enabled is False
    assert resolved.preset == "strict"
    assert resolved.capabilities == ("files", "shell", "memory")
    assert resolved.file_access_mode == "read_write"
    assert resolved.workspace_scope_mode == "profile_only"
