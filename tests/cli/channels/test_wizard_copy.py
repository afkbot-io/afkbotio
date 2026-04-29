"""Channel wizard copy tests."""

from pytest import MonkeyPatch

from afkbot.cli.commands import channel_shared
from afkbot.cli.commands.channel_prompt_support import _channel_choice_label
from afkbot.cli.commands.channel_shared import collect_channel_access_policy_inputs
from afkbot.cli.commands.channel_telethon_commands.common import (
    TELETHON_REPLY_MODE_LABEL_OVERRIDES,
)
from afkbot.cli.presentation.prompt_i18n import PromptLanguage


def test_channel_choice_labels_explain_raw_tool_profile_values() -> None:
    """Channel tool-profile values should render with beginner-friendly descriptions."""

    assert _channel_choice_label("inherit", lang=PromptLanguage.EN) == (
        "inherit - use the profile's full tool ceiling for this channel"
    )
    assert _channel_choice_label("support_readonly", lang=PromptLanguage.RU) == (
        "support_readonly - messaging_safe плюс чтение и поиск по файлам"
    )


def test_channel_choice_labels_explain_access_and_session_values_in_russian() -> None:
    """Access and session policy values should not appear as unexplained raw tokens."""

    assert _channel_choice_label("allowlist", lang=PromptLanguage.RU) == (
        "allowlist - разрешить только ID, которые вы введёте дальше"
    )
    assert _channel_choice_label("per-user-in-group", lang=PromptLanguage.RU) == (
        "per-user-in-group - отдельная беседа для каждого пользователя в группе"
    )


def test_telethon_reply_mode_disabled_label_is_read_only_not_access_rejection() -> None:
    """Telethon reply mode uses disabled as read-only mode, not as a chat access block."""

    assert _channel_choice_label(
        "disabled",
        lang=PromptLanguage.RU,
        label_overrides=TELETHON_REPLY_MODE_LABEL_OVERRIDES,
    ) == "disabled - только читать входящие сообщения, не отправлять ответы"
    assert _channel_choice_label("disabled", lang=PromptLanguage.RU) == (
        "disabled - полностью запретить этот тип чата"
    )


def test_channel_access_wizard_prompts_outbound_allowlist_for_send_profiles(
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive channel setup should expose outbound channel.send allowlist controls."""

    bool_prompts: list[str] = []
    text_prompts: list[str] = []

    def _fake_bool(**kwargs: object) -> bool:
        bool_prompts.append(str(kwargs["prompt_en"]))
        return True

    def _fake_text(**kwargs: object) -> str:
        text_prompts.append(str(kwargs["prompt_en"]))
        return "12345"

    monkeypatch.setattr(channel_shared, "resolve_channel_bool", _fake_bool)
    monkeypatch.setattr(channel_shared, "resolve_channel_text", _fake_text)

    access = collect_channel_access_policy_inputs(
        interactive=True,
        lang=PromptLanguage.EN,
        private_policy="disabled",
        allow_from=None,
        group_policy="disabled",
        groups=None,
        group_allow_from=None,
        outbound_allow_to=None,
        tool_profile="messaging_safe",
    )

    assert access.outbound_allow_to == ("12345",)
    assert bool_prompts == ["Restrict channel.send outbound targets?"]
    assert text_prompts == ["Allowed outbound chat/user ids"]
