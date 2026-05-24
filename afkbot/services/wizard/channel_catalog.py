"""Channel wizard inventory and scenario catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.channels.tool_profiles import ChannelToolProfile
from afkbot.services.wizard.contracts import (
    WizardBranch,
    WizardPlan,
    WizardQuestion,
    WizardQuestionKind,
)

ChannelTransportId = Literal["telegram", "telethon", "partyflow"]


@dataclass(frozen=True, slots=True)
class ChannelWizardScenario:
    """User-facing channel intent mapped to existing channel fields."""

    id: str
    transport: ChannelTransportId
    label_en: str
    label_ru: str
    tool_profile: ChannelToolProfile
    private_policy: str = "allowlist"
    group_policy: str = "disabled"
    trigger_mode: str | None = None
    reply_mode: str | None = None
    session_policy: str = "per-chat"
    watcher_enabled: bool | None = None
    current_channel_tools: tuple[str, ...] = ("channel.history.list", "channel.send")


_CHANNEL_SCENARIOS: dict[str, ChannelWizardScenario] = {
    "telegram_private_dm": ChannelWizardScenario(
        id="telegram_private_dm",
        transport="telegram",
        label_en="Telegram private DM bot",
        label_ru="Telegram-бот в личке",
        tool_profile="messaging_safe",
        trigger_mode="mention_or_reply",
    ),
    "telegram_group_mention": ChannelWizardScenario(
        id="telegram_group_mention",
        transport="telegram",
        label_en="Telegram group mention bot",
        label_ru="Telegram-бот в группе по упоминанию",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="allowlist",
        trigger_mode="mention_or_reply",
        session_policy="per-thread",
    ),
    "telegram_group_all_messages": ChannelWizardScenario(
        id="telegram_group_all_messages",
        transport="telegram",
        label_en="Telegram group all allowed messages",
        label_ru="Telegram-группа: все разрешённые сообщения",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="allowlist",
        trigger_mode="all_messages",
        session_policy="per-thread",
    ),
    "telegram_trusted_admin": ChannelWizardScenario(
        id="telegram_trusted_admin",
        transport="telegram",
        label_en="Telegram trusted admin channel",
        label_ru="Telegram доверенный админ-канал",
        tool_profile="inherit",
        private_policy="open",
        group_policy="allowlist",
        trigger_mode="mention_or_reply",
        session_policy="per-thread",
    ),
    "partyflow_private_mention": ChannelWizardScenario(
        id="partyflow_private_mention",
        transport="partyflow",
        label_en="PartyFlow group mention bot",
        label_ru="PartyFlow-бот в группе по упоминанию",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="open",
        trigger_mode="mention",
        reply_mode="same_conversation",
        session_policy="per-thread",
    ),
    "partyflow_group_keywords": ChannelWizardScenario(
        id="partyflow_group_keywords",
        transport="partyflow",
        label_en="PartyFlow group keyword bot",
        label_ru="PartyFlow-бот в группе по ключевым словам",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="open",
        trigger_mode="keywords",
        reply_mode="same_conversation",
        session_policy="per-thread",
    ),
    "partyflow_group_all_messages": ChannelWizardScenario(
        id="partyflow_group_all_messages",
        transport="partyflow",
        label_en="PartyFlow all allowed group messages",
        label_ru="PartyFlow: все разрешённые сообщения группы",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="allowlist",
        trigger_mode="all",
        reply_mode="same_conversation",
        session_policy="per-thread",
    ),
    "partyflow_trusted_admin": ChannelWizardScenario(
        id="partyflow_trusted_admin",
        transport="partyflow",
        label_en="PartyFlow trusted admin channel",
        label_ru="PartyFlow доверенный админ-канал",
        tool_profile="inherit",
        private_policy="open",
        group_policy="allowlist",
        trigger_mode="mention",
        reply_mode="same_conversation",
        session_policy="per-thread",
    ),
    "telethon_private_reply": ChannelWizardScenario(
        id="telethon_private_reply",
        transport="telethon",
        label_en="Telethon private reply assistant",
        label_ru="Telethon личный помощник с ответами",
        tool_profile="messaging_safe",
        private_policy="allowlist",
        group_policy="disabled",
        trigger_mode="reply_or_command",
        reply_mode="same_chat",
        session_policy="per-chat",
        watcher_enabled=False,
    ),
    "telethon_group_command": ChannelWizardScenario(
        id="telethon_group_command",
        transport="telethon",
        label_en="Telethon group replies or commands",
        label_ru="Telethon группа: ответы или команды",
        tool_profile="messaging_safe",
        private_policy="disabled",
        group_policy="allowlist",
        trigger_mode="reply_or_command",
        reply_mode="same_chat",
        session_policy="per-thread",
        watcher_enabled=False,
    ),
    "telethon_watcher_digest": ChannelWizardScenario(
        id="telethon_watcher_digest",
        transport="telethon",
        label_en="Telethon watcher digest",
        label_ru="Telethon дайджест наблюдателя",
        tool_profile="chat_minimal",
        private_policy="disabled",
        group_policy="disabled",
        reply_mode="disabled",
        session_policy="main",
        watcher_enabled=True,
        current_channel_tools=("channel.history.list",),
    ),
    "telethon_trusted_admin": ChannelWizardScenario(
        id="telethon_trusted_admin",
        transport="telethon",
        label_en="Telethon trusted admin account",
        label_ru="Telethon доверенный админ-аккаунт",
        tool_profile="inherit",
        private_policy="open",
        group_policy="allowlist",
        trigger_mode="reply_or_command",
        reply_mode="same_chat",
        session_policy="per-thread",
        watcher_enabled=False,
    ),
}


_TOOL_PROFILE_LABELS: dict[str, tuple[str, str]] = {
    "inherit": (
        "Use the profile's full permissions - dangerous for untrusted chats",
        "Полные права профиля - опасно для недоверенных чатов",
    ),
    "chat_minimal": (
        "Minimal chat - replies and current-channel history, no general tools",
        "Минимальный чат - ответы и история текущего канала, без общих инструментов",
    ),
    "messaging_safe": (
        "Safe messaging - channel history, channel.send, and safe memory tools",
        "Безопасные сообщения - история канала, channel.send и безопасная память",
    ),
    "support_readonly": (
        "Support read-only - messaging plus read-only file search/read",
        "Support только чтение - сообщения плюс чтение и поиск файлов",
    ),
    "taskflow_operator": (
        "Task operator - create and update tasks from the channel, no terminal or files",
        "Задачи из канала - создавать и обновлять задачи, без терминала и файлов",
    ),
}


def channel_scenario(scenario_id: str) -> ChannelWizardScenario:
    """Return one channel wizard scenario by id."""

    try:
        return _CHANNEL_SCENARIOS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"Unknown channel wizard scenario: {scenario_id}") from exc


def list_channel_scenarios(*, transport: str) -> tuple[ChannelWizardScenario, ...]:
    """Return scenarios for one channel transport in stable order."""

    normalized = transport.strip().lower()
    return tuple(
        scenario for scenario in _CHANNEL_SCENARIOS.values() if scenario.transport == normalized
    )


def channel_tool_profile_label(tool_profile: str, *, lang: PromptLanguage) -> str:
    """Return centralized user-facing label for one channel tool profile."""

    labels = _TOOL_PROFILE_LABELS.get(tool_profile)
    if labels is None:
        return tool_profile
    return labels[1] if lang == PromptLanguage.RU else labels[0]


def channel_plan(transport: str) -> WizardPlan:
    """Return the current channel wizard inventory for one transport."""

    normalized = transport.strip().lower()
    if normalized not in {"telegram", "telethon", "partyflow"}:
        raise ValueError(f"Unsupported channel wizard transport: {transport}")
    questions = [
        _question("channel_scenario", "base", "single", "Channel: Scenario", "Канал: Сценарий"),
        _question("channel_id", "base", "text", "Channel id", "Идентификатор канала"),
        _question("profile", "base", "single", "Channel: Profile", "Канал: Профиль"),
        _question("enabled", "base", "confirm", "Enable channel?", "Включить канал?"),
        _question(
            "channel_tool_profile",
            "base",
            "single",
            "What can the agent do from this channel?",
            "Что агент может делать из этого канала?",
        ),
        _question(
            "routing_binding",
            "routing",
            "confirm",
            "Create matching routing binding?",
            "Создать привязку маршрутизации?",
        ),
        _question(
            "session_policy",
            "routing",
            "single",
            "How should conversations be grouped?",
            "Как группировать диалоги",
            shown_when="routing_binding",
        ),
        _question(
            "private_access", "access", "single", "Private chat access", "Доступ в личных чатах"
        ),
        _question(
            "private_allowlist",
            "access",
            "text",
            "Allowed private sender ids",
            "ID отправителей для личных чатов",
            shown_when="private_access == allowlist",
        ),
        _question("group_access", "access", "single", "Group access", "Доступ в группах"),
        _question(
            "group_allowlist",
            "access",
            "text",
            "Allowed group ids",
            "ID групп/каналов",
            shown_when="group_access == allowlist",
        ),
        _question(
            "group_sender_allowlist",
            "access",
            "text",
            "Allowed group sender ids",
            "ID отправителей в группах",
            shown_when="group_access == allowlist",
        ),
        _question(
            "outbound_send_targets",
            "access",
            "text",
            "Allowed outbound chat/user ids",
            "Chat/user ID для исходящих сообщений",
            shown_when="tool_profile_may_send",
        ),
        _question(
            "ingress_batch",
            "runtime",
            "confirm",
            "Merge message bursts before replying?",
            "Объединять всплески сообщений перед ответом?",
        ),
        _question(
            "ingress_debounce",
            "runtime",
            "integer",
            "Quiet window before merge (ms)",
            "Окно тишины перед объединением (мс)",
            shown_when="ingress_batch",
        ),
    ]
    if normalized == "telegram":
        questions.extend(
            (
                _question(
                    "telegram_group_trigger",
                    "transport",
                    "single",
                    "Telegram group trigger mode",
                    "Режим триггера для Telegram групп",
                ),
                _question(
                    "telegram_bot_token",
                    "credentials",
                    "secret",
                    "Telegram bot token",
                    "Токен Telegram-бота",
                ),
                _question(
                    "telegram_default_chat_id",
                    "credentials",
                    "secret",
                    "Default Telegram chat id",
                    "Telegram chat id по умолчанию",
                    advanced=True,
                ),
            )
        )
    elif normalized == "telethon":
        questions.extend(
            (
                _question(
                    "telethon_reply_mode",
                    "transport",
                    "single",
                    "Telethon reply mode",
                    "Режим ответов Telethon",
                ),
                _question(
                    "telethon_group_invocation",
                    "transport",
                    "single",
                    "Telethon group invocation mode",
                    "Режим вызова Telethon в группах",
                ),
                _question(
                    "telethon_self_commands",
                    "transport",
                    "confirm",
                    "Process self commands?",
                    "Обрабатывать собственные команды?",
                ),
                _question(
                    "telethon_command_prefix",
                    "transport",
                    "text",
                    "Command prefix",
                    "Префикс команды",
                    shown_when="telethon_self_commands",
                ),
                _question(
                    "telethon_watcher_digest",
                    "transport",
                    "confirm",
                    "Enable watcher digests?",
                    "Включить дайджесты наблюдателя?",
                ),
                _question(
                    "telethon_api_id", "credentials", "secret", "Telethon API id", "Telethon API id"
                ),
                _question(
                    "telethon_api_hash",
                    "credentials",
                    "secret",
                    "Telethon API hash",
                    "Telethon API hash",
                ),
                _question(
                    "telethon_phone", "credentials", "secret", "Telegram phone", "Телефон Telegram"
                ),
            )
        )
    else:
        questions.extend(
            (
                _question(
                    "partyflow_trigger_mode",
                    "transport",
                    "single",
                    "PartyFlow trigger mode",
                    "Режим триггера PartyFlow",
                ),
                _question(
                    "partyflow_trigger_keywords",
                    "transport",
                    "text",
                    "PartyFlow trigger keywords",
                    "Ключевые слова-триггеры PartyFlow",
                    shown_when="partyflow_trigger_mode == keywords",
                ),
                _question(
                    "partyflow_reply_mode",
                    "transport",
                    "single",
                    "PartyFlow reply mode",
                    "Режим ответа PartyFlow",
                ),
                _question(
                    "partyflow_bot_token",
                    "credentials",
                    "secret",
                    "PartyFlow bot token",
                    "Токен бота PartyFlow",
                ),
            )
        )
    return WizardPlan(
        id=f"channel_{normalized}",
        title_en=f"{normalized.title()} Channel Wizard",
        title_ru=f"Мастер канала {normalized}",
        questions=tuple(questions),
        branches=(
            _branch(
                "scenario_defaults",
                "channel_scenario != custom",
                (
                    "channel_tool_profile",
                    "session_policy",
                    "private_access",
                    "group_access",
                ),
                "Scenario defaults",
                "Значения сценария",
            ),
            _branch(
                "trusted_admin",
                "channel_scenario endswith trusted_admin",
                (
                    "channel_tool_profile",
                    "private_access",
                    "group_access",
                    "outbound_send_targets",
                ),
                "Trusted admin surface",
                "Доверенная админ-поверхность",
            ),
            _branch(
                "routing_enabled",
                "routing_binding",
                ("session_policy",),
                "Routing branch",
                "Ветка маршрутизации",
            ),
            _branch(
                "access_allowlists",
                "private_access == allowlist or group_access == allowlist",
                (
                    "private_allowlist",
                    "group_allowlist",
                    "group_sender_allowlist",
                ),
                "Access allowlists",
                "Allowlist доступа",
            ),
            _branch(
                "ingress_batch",
                "ingress_batch",
                ("ingress_debounce",),
                "Ingress batching",
                "Пакетирование входящих",
            ),
        ),
    )


def _question(
    question_id: str,
    section: str,
    kind: str,
    title_en: str,
    title_ru: str,
    *,
    shown_when: str | None = None,
    advanced: bool = False,
) -> WizardQuestion:
    return WizardQuestion(
        id=question_id,
        section=section,
        kind=cast(WizardQuestionKind, kind),
        title_en=title_en,
        title_ru=title_ru,
        prompt_en=title_en,
        prompt_ru=title_ru,
        shown_when=shown_when,
        advanced=advanced,
    )


def _branch(
    branch_id: str,
    condition: str,
    question_ids: tuple[str, ...],
    label_en: str,
    label_ru: str,
) -> WizardBranch:
    return WizardBranch(
        id=branch_id,
        condition=condition,
        question_ids=question_ids,
        label_en=label_en,
        label_ru=label_ru,
    )
