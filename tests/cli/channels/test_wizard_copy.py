"""Channel wizard copy tests."""

from pytest import MonkeyPatch

from afkbot.cli.commands import channel_credentials_support
from afkbot.cli.commands import channel_prompt_support
from afkbot.cli.commands import channel_shared
from afkbot.cli.commands.channel_prompt_support import (
    _channel_choice_label,
    resolve_channel_setup_scenario,
)
from afkbot.cli.commands.channel_shared import collect_channel_access_policy_inputs
from afkbot.cli.commands.channel_shared import resolve_channel_tool_profile_value
from afkbot.cli.commands.channel_telethon_commands.common import (
    TELETHON_REPLY_MODE_LABEL_OVERRIDES,
)
from afkbot.cli.presentation.prompt_i18n import PromptLanguage


def test_channel_choice_labels_explain_raw_tool_profile_values() -> None:
    """Channel tool-profile values should render with beginner-friendly descriptions."""

    assert _channel_choice_label("inherit", lang=PromptLanguage.EN) == (
        "Use the profile's full permissions - dangerous for untrusted chats"
    )
    assert _channel_choice_label("chat_minimal", lang=PromptLanguage.RU) == (
        "Минимальный чат - ответы и история текущего канала, без общих инструментов"
    )
    assert _channel_choice_label("support_readonly", lang=PromptLanguage.RU) == (
        "Support только чтение - сообщения плюс чтение и поиск файлов"
    )
    assert _channel_choice_label("taskflow_operator", lang=PromptLanguage.RU) == (
        "Задачи из канала - создавать и обновлять задачи, без терминала и файлов"
    )


def test_channel_choice_labels_explain_access_and_session_values_in_russian() -> None:
    """Access and session policy values should not appear as unexplained raw tokens."""

    assert _channel_choice_label("allowlist", lang=PromptLanguage.RU) == (
        "Разрешить только ID, которые вы введёте дальше"
    )
    assert _channel_choice_label("per-user-in-group", lang=PromptLanguage.RU) == (
        "Отдельная беседа для каждого участника группы"
    )


def test_channel_setup_scenario_only_prompts_in_real_tty(
    monkeypatch: MonkeyPatch,
) -> None:
    """Scenario selection should not consume scripted CliRunner input."""

    monkeypatch.setattr(channel_prompt_support.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(channel_prompt_support.sys.stdout, "isatty", lambda: False)

    assert (
        resolve_channel_setup_scenario(
            transport="partyflow",
            interactive=True,
            lang=PromptLanguage.EN,
        )
        is None
    )


def test_channel_setup_scenario_returns_selected_defaults_in_tty(
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive scenario selection should return reusable channel defaults."""

    monkeypatch.setattr(channel_prompt_support.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(channel_prompt_support.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        channel_prompt_support,
        "run_inline_single_select",
        lambda **_kwargs: "partyflow_private_mention",
    )

    scenario = resolve_channel_setup_scenario(
        transport="partyflow",
        interactive=True,
        lang=PromptLanguage.EN,
    )

    assert scenario is not None
    assert scenario.tool_profile == "messaging_safe"
    assert scenario.trigger_mode == "mention"
    assert scenario.reply_mode == "same_conversation"


def test_telethon_reply_mode_disabled_label_is_read_only_not_access_rejection() -> None:
    """Telethon reply mode uses disabled as read-only mode, not as a chat access block."""

    assert (
        _channel_choice_label(
            "disabled",
            lang=PromptLanguage.RU,
            label_overrides=TELETHON_REPLY_MODE_LABEL_OVERRIDES,
        )
        == "disabled - только читать входящие сообщения, не отправлять ответы"
    )
    assert (
        _channel_choice_label("disabled", lang=PromptLanguage.RU)
        == "Полностью запретить этот тип чата"
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
    assert bool_prompts == ["Limit proactive channel.send targets?"]
    assert text_prompts == ["Allowed outbound chat/user ids"]


def test_channel_tool_profile_prompt_is_shared_and_explains_permission_ceiling(
    monkeypatch: MonkeyPatch,
) -> None:
    """Channel add/update flows should use one permission-narrowing prompt."""

    prompts: list[dict[str, object]] = []

    def _fake_choice(**kwargs: object) -> str:
        prompts.append(dict(kwargs))
        return "support_readonly"

    monkeypatch.setattr(channel_shared, "resolve_channel_choice", _fake_choice)

    resolved = resolve_channel_tool_profile_value(
        value=None,
        interactive=True,
        default="messaging_safe",
        lang=PromptLanguage.EN,
    )

    assert resolved == "support_readonly"
    assert prompts[0]["prompt_en"] == "What can the agent do from this channel?"
    assert "profile remains the maximum permission ceiling" in str(prompts[0]["detail_en"])


def test_partyflow_credentials_wizard_only_prompts_for_bot_token(
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow polling setup should only ask for bot token credentials."""

    secret_prompts: list[dict[str, object]] = []

    def _fake_secret(**kwargs: object) -> str | None:
        secret_prompts.append(dict(kwargs))
        return "fri_bot_test"

    monkeypatch.setattr(
        channel_credentials_support,
        "existing_channel_credential_names",
        lambda **_kwargs: set(),
    )
    monkeypatch.setattr(channel_credentials_support, "resolve_channel_secret", _fake_secret)
    monkeypatch.setattr(channel_credentials_support, "_upsert_app_secret", lambda **_kwargs: None)
    monkeypatch.setattr(channel_credentials_support.typer, "echo", lambda *_args, **_kwargs: None)

    updated = channel_credentials_support.configure_partyflow_channel_credentials(
        settings=object(),  # type: ignore[arg-type]
        profile_id="default",
        credential_profile_key="ops-partyflow",
        interactive=True,
        lang=PromptLanguage.RU,
    )

    assert updated is True
    assert [item["prompt_en"] for item in secret_prompts] == ["PartyFlow bot token"]
