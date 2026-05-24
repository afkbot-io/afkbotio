"""Intent-first setup/profile wizard catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.cli.presentation.prompt_i18n import PromptLanguage

ProfileIntentDepth = Literal["quick", "guided", "expert"]
ProfileIntentWorkContext = Literal["channels", "project", "automations", "sandbox", "expert"]
ProfileIntentAction = Literal[
    "reply",
    "channel_history",
    "channel_send",
    "taskflow",
    "automation",
    "project_read",
    "project_write",
    "sandbox_write",
    "shell_allowlist",
    "internet_docs",
    "browser",
    "external_services",
    "memory",
    "subagents",
    "credentials",
    "afkbot_admin",
]
ProfileIntentIsolation = Literal[
    "no_files",
    "profile_only",
    "project_read",
    "project_write",
    "profile_shell",
    "project_shell",
    "danger_full_system",
]
ProfileIntentConfirmation = Literal["fast", "balanced", "strict"]
ProfileIntentNetwork = Literal["deny_all", "recommended", "custom", "unrestricted"]


@dataclass(frozen=True, slots=True)
class ProfileIntentChoice:
    """One localized high-level setup/profile wizard choice."""

    id: str
    label_en: str
    label_ru: str
    description_en: str
    description_ru: str
    expert_only: bool = False
    dangerous: bool = False

    def label(self, *, lang: PromptLanguage) -> str:
        """Return localized label plus a concise explanation for CLI selectors."""

        if lang == PromptLanguage.RU:
            return f"{self.label_ru} - {self.description_ru}"
        return f"{self.label_en} - {self.description_en}"


_DEPTHS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="quick",
        label_en="Quick safe setup",
        label_ru="Быстрая безопасная настройка",
        description_en="AFKBOT answers chats/channels and can keep task memory; no files or shell",
        description_ru="бот отвечает в чатах/каналах и ведёт задачи; без файлов и терминала",
    ),
    ProfileIntentChoice(
        id="guided",
        label_en="Guided scenario setup",
        label_ru="Настройка по сценариям",
        description_en="choose where the bot works, what it may do, and its isolation level",
        description_ru="выбрать, где бот работает, что ему можно делать и как его изолировать",
    ),
    ProfileIntentChoice(
        id="expert",
        label_en="Detailed manual setup",
        label_ru="Вручную: детальная настройка",
        description_en="review every low-level capability, file, terminal, and network permission",
        description_ru="проверить каждую возможность, файлы, терминал и сеть отдельно",
        expert_only=True,
    ),
)

_WORK_CONTEXTS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="channels",
        label_en="Channels and chats",
        label_ru="Каналы и чаты",
        description_en="Telegram, PartyFlow, and other conversations",
        description_ru="Telegram, PartyFlow и другие диалоги",
    ),
    ProfileIntentChoice(
        id="project",
        label_en="Current project",
        label_ru="Текущий проект",
        description_en="read or edit files only inside this project when allowed later",
        description_ru="читать или менять файлы только в этом проекте, если разрешите дальше",
    ),
    ProfileIntentChoice(
        id="automations",
        label_en="Automations",
        label_ru="Автоматизации",
        description_en="scheduled jobs and incoming webhooks",
        description_ru="запуски по расписанию и входящие вебхуки",
    ),
    ProfileIntentChoice(
        id="sandbox",
        label_en="Private working folder",
        label_ru="Личная рабочая папка",
        description_en="create notes, reports, and temporary files inside the profile folder",
        description_ru="создавать заметки, отчёты и временные файлы внутри папки профиля",
    ),
    ProfileIntentChoice(
        id="expert",
        label_en="System administration",
        label_ru="Системное администрирование",
        description_en="dangerous path for profile settings, secrets, integrations, and full local access",
        description_ru="опасный режим для настроек профилей, секретов, интеграций и полного локального доступа",
        expert_only=True,
        dangerous=True,
    ),
)

_ACTIONS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="reply",
        label_en="Reply to people",
        label_ru="Отвечать людям",
        description_en="write answers in the active chat or channel",
        description_ru="писать ответы в активный чат или канал",
    ),
    ProfileIntentChoice(
        id="channel_history",
        label_en="Read current channel history",
        label_ru="Читать историю текущего канала",
        description_en="use messages from the active conversation as context",
        description_ru="использовать сообщения активного диалога как контекст",
    ),
    ProfileIntentChoice(
        id="channel_send",
        label_en="Send to allowed conversations",
        label_ru="Писать в разрешённые диалоги",
        description_en="send outbound messages only through configured channel rules",
        description_ru="отправлять исходящие сообщения только по правилам канала",
    ),
    ProfileIntentChoice(
        id="taskflow",
        label_en="Create and update tasks",
        label_ru="Создавать и обновлять задачи",
        description_en="manage durable Task Flow items for this profile",
        description_ru="вести список задач только в рамках этого профиля",
    ),
    ProfileIntentChoice(
        id="automation",
        label_en="Run background work",
        label_ru="Запускать фоновые сценарии",
        description_en="use configured schedules and webhooks",
        description_ru="использовать настроенные расписания и вебхуки",
    ),
    ProfileIntentChoice(
        id="project_read",
        label_en="Read project files",
        label_ru="Читать файлы проекта",
        description_en="search and inspect files without editing them",
        description_ru="искать и просматривать файлы без изменений",
    ),
    ProfileIntentChoice(
        id="project_write",
        label_en="Change project files",
        label_ru="Менять файлы проекта",
        description_en="create and edit files inside the project boundary",
        description_ru="создавать и изменять файлы внутри границы проекта",
    ),
    ProfileIntentChoice(
        id="sandbox_write",
        label_en="Create files in the private folder",
        label_ru="Создавать файлы в личной папке",
        description_en="write only inside the profile sandbox",
        description_ru="писать только внутри изолированной папки профиля",
    ),
    ProfileIntentChoice(
        id="shell_allowlist",
        label_en="Run approved terminal commands",
        label_ru="Запускать разрешённые команды терминала",
        description_en="only selected commands and only inside the configured boundary",
        description_ru="только выбранные команды и только внутри заданной границы",
    ),
    ProfileIntentChoice(
        id="internet_docs",
        label_en="Read internet documentation",
        label_ru="Читать документацию в интернете",
        description_en="search and fetch web pages through the network policy",
        description_ru="искать и открывать веб-страницы по сетевой политике",
    ),
    ProfileIntentChoice(
        id="browser",
        label_en="Use a browser",
        label_ru="Работать с браузером",
        description_en="open pages and interact with websites when enabled",
        description_ru="открывать страницы и взаимодействовать с сайтами, если разрешено",
    ),
    ProfileIntentChoice(
        id="external_services",
        label_en="Call external HTTP services",
        label_ru="Вызывать внешние HTTP-сервисы",
        description_en="send outbound requests to domains allowed by the network boundary",
        description_ru="отправлять исходящие запросы на домены, разрешённые сетевой границей",
    ),
    ProfileIntentChoice(
        id="memory",
        label_en="Remember useful context",
        label_ru="Запоминать полезный контекст",
        description_en="store and search profile memory",
        description_ru="сохранять и искать память профиля",
    ),
    ProfileIntentChoice(
        id="subagents",
        label_en="Delegate to allowed helpers",
        label_ru="Передавать работу разрешённым помощникам",
        description_en="run configured subagents when the profile allows it",
        description_ru="запускать настроенных субагентов, если профиль это разрешает",
        expert_only=True,
    ),
    ProfileIntentChoice(
        id="credentials",
        label_en="Manage secrets",
        label_ru="Управлять секретами",
        description_en="read and change encrypted credential bindings",
        description_ru="читать и менять зашифрованные привязки credentials",
        expert_only=True,
        dangerous=True,
    ),
    ProfileIntentChoice(
        id="afkbot_admin",
        label_en="Change AFKBOT settings",
        label_ru="Менять настройки AFKBOT",
        description_en="profiles, channels, plugins, skills, MCP, and app integrations",
        description_ru="профили, каналы, плагины, навыки, MCP и app-интеграции",
        expert_only=True,
        dangerous=True,
    ),
)

_ISOLATIONS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="no_files",
        label_en="No file access",
        label_ru="Без доступа к файлам",
        description_en="the bot cannot read or change local files",
        description_ru="бот не читает и не меняет локальные файлы",
    ),
    ProfileIntentChoice(
        id="profile_only",
        label_en="Only the profile private folder",
        label_ru="Только личная папка профиля",
        description_en="file work stays inside the profile sandbox",
        description_ru="работа с файлами остаётся внутри изолированной папки профиля",
    ),
    ProfileIntentChoice(
        id="project_read",
        label_en="Read-only current project",
        label_ru="Только чтение текущего проекта",
        description_en="inspect this project without editing files",
        description_ru="просматривать проект без изменения файлов",
    ),
    ProfileIntentChoice(
        id="project_write",
        label_en="Read and change current project",
        label_ru="Чтение и изменение текущего проекта",
        description_en="file edits are limited to the project boundary",
        description_ru="изменения файлов ограничены границей проекта",
    ),
    ProfileIntentChoice(
        id="profile_shell",
        label_en="Terminal only in the private folder",
        label_ru="Терминал только в личной папке",
        description_en="approved commands run through an OS sandbox when available",
        description_ru="разрешённые команды запускаются через системную изоляцию, когда она доступна",
    ),
    ProfileIntentChoice(
        id="project_shell",
        label_en="Terminal only in the project",
        label_ru="Терминал только в текущем проекте",
        description_en="approved commands run inside the project boundary",
        description_ru="разрешённые команды запускаются внутри границы проекта",
        expert_only=True,
    ),
    ProfileIntentChoice(
        id="danger_full_system",
        label_en="Full local access",
        label_ru="Полный локальный доступ",
        description_en="dangerous: all local files and trusted terminal behavior",
        description_ru="опасно: все локальные файлы и доверенное поведение терминала",
        expert_only=True,
        dangerous=True,
    ),
)

_CONFIRMATIONS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="fast",
        label_en="Fewer confirmations",
        label_ru="Меньше подтверждений",
        description_en="faster work, still bounded by profile policy",
        description_ru="быстрее работать, но в рамках политики профиля",
    ),
    ProfileIntentChoice(
        id="balanced",
        label_en="Balanced confirmations",
        label_ru="Сбалансированные подтверждения",
        description_en="ask before dangerous file changes and critical actions",
        description_ru="спрашивать перед опасными изменениями файлов и критичными действиями",
    ),
    ProfileIntentChoice(
        id="strict",
        label_en="Confirm critical actions",
        label_ru="Подтверждать критичные действия",
        description_en="more checks before risky operations",
        description_ru="больше проверок перед рискованными операциями",
    ),
)

_NETWORKS: tuple[ProfileIntentChoice, ...] = (
    ProfileIntentChoice(
        id="deny_all",
        label_en="No network tools",
        label_ru="Без сетевых инструментов",
        description_en="block web and HTTP tools",
        description_ru="запретить веб-страницы и исходящие запросы",
    ),
    ProfileIntentChoice(
        id="recommended",
        label_en="Only needed service domains",
        label_ru="Только нужные домены сервисов",
        description_en="allow known provider and integration hosts",
        description_ru="разрешить известные хосты провайдеров и интеграций",
    ),
    ProfileIntentChoice(
        id="custom",
        label_en="Only listed domains",
        label_ru="Только указанные домены",
        description_en="enter the exact domains this profile may contact",
        description_ru="ввести точные домены, к которым профиль может обращаться",
    ),
    ProfileIntentChoice(
        id="unrestricted",
        label_en="Any network host",
        label_ru="Любые сетевые адреса",
        description_en="dangerous: do not use for untrusted channel profiles",
        description_ru="опасно: не используйте для недоверенных каналов",
        dangerous=True,
    ),
)


def list_profile_intent_depths() -> tuple[ProfileIntentChoice, ...]:
    """Return setup depth choices."""

    return _DEPTHS


def list_profile_intent_work_contexts(
    *, include_expert: bool = True
) -> tuple[ProfileIntentChoice, ...]:
    """Return work-context choices."""

    return _filter_choices(
        _WORK_CONTEXTS, include_expert=include_expert, include_dangerous=include_expert
    )


def list_profile_intent_actions(*, include_expert: bool = True) -> tuple[ProfileIntentChoice, ...]:
    """Return allowed-action choices."""

    return _filter_choices(
        _ACTIONS, include_expert=include_expert, include_dangerous=include_expert
    )


def list_profile_intent_isolations(
    *, include_dangerous: bool = True
) -> tuple[ProfileIntentChoice, ...]:
    """Return isolation choices."""

    return _filter_choices(
        _ISOLATIONS, include_expert=include_dangerous, include_dangerous=include_dangerous
    )


def list_profile_intent_confirmations() -> tuple[ProfileIntentChoice, ...]:
    """Return confirmation-mode choices."""

    return _CONFIRMATIONS


def list_profile_intent_networks(
    *, include_dangerous: bool = True
) -> tuple[ProfileIntentChoice, ...]:
    """Return network choices."""

    return _filter_choices(_NETWORKS, include_expert=True, include_dangerous=include_dangerous)


def profile_intent_default_actions(work_contexts: tuple[str, ...]) -> tuple[str, ...]:
    """Return useful safe action defaults for selected work contexts."""

    contexts = {item.strip().lower() for item in work_contexts if item.strip()}
    actions: list[str] = ["memory"]
    if "channels" in contexts:
        actions.extend(("reply", "channel_history", "taskflow"))
    if "project" in contexts:
        actions.append("project_read")
    if "automations" in contexts:
        actions.extend(("automation", "taskflow"))
    if "sandbox" in contexts:
        actions.append("sandbox_write")
    if "expert" in contexts:
        actions.extend(("subagents", "credentials", "afkbot_admin"))
    return tuple(dict.fromkeys(actions))


def profile_intent_action_choices_for_contexts(
    work_contexts: tuple[str, ...],
    *,
    include_expert: bool = False,
) -> tuple[ProfileIntentChoice, ...]:
    """Return action choices relevant to the selected work contexts."""

    contexts = {item.strip().lower() for item in work_contexts if item.strip()}
    if not contexts:
        contexts = {"channels"}
    allowed: set[str] = {"memory"}
    if "channels" in contexts:
        allowed.update(("reply", "channel_history", "channel_send", "taskflow"))
    if "project" in contexts:
        allowed.update(
            ("project_read", "project_write", "taskflow", "internet_docs", "external_services")
        )
    if "automations" in contexts:
        allowed.update(("automation", "taskflow", "internet_docs", "external_services"))
    if "sandbox" in contexts:
        allowed.update(("sandbox_write", "shell_allowlist", "taskflow"))
    if include_expert or "expert" in contexts:
        allowed.update(("subagents", "credentials", "afkbot_admin"))
    return tuple(
        choice
        for choice in list_profile_intent_actions(include_expert=include_expert)
        if choice.id in allowed
    )


def quick_safe_profile_intent_defaults() -> dict[str, object]:
    """Return the no-files/no-shell default used by recommended setup."""

    return {
        "depth": "quick",
        "work_contexts": ("channels",),
        "actions": ("reply", "channel_history", "taskflow", "memory"),
        "isolation": "no_files",
        "confirmation": "balanced",
        "network": "recommended",
    }


def _filter_choices(
    choices: tuple[ProfileIntentChoice, ...],
    *,
    include_expert: bool,
    include_dangerous: bool,
) -> tuple[ProfileIntentChoice, ...]:
    return tuple(
        choice
        for choice in choices
        if (include_expert or not choice.expert_only)
        and (include_dangerous or not choice.dangerous)
    )
