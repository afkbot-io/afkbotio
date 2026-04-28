"""Policy/security prompt helpers for interactive setup flow."""

from __future__ import annotations

from typing import Final

import typer

from afkbot.cli.presentation.inline_select import (
    confirm_space,
    run_inline_single_select,
    select_multi_option_dialog,
)
from afkbot.cli.presentation.prompt_i18n import (
    PromptLanguage,
    msg,
    multi_hint,
    no_label,
    single_hint,
    yes_label,
)
from afkbot.services.policy import (
    PolicyCapabilityId,
    default_capabilities_for_preset,
    list_capability_specs,
    parse_preset_level,
)

POLICY_PRESET_CHOICES: Final[tuple[str, ...]] = ("simple", "medium", "strict")


def prompt_policy_setup_mode(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt high-level security wizard mode: recommended or custom."""

    return select_value_dialog(
        title=msg(lang, en="Setup: Security setup", ru="Настройка: Безопасность"),
        text=msg(
            lang,
            en=(
                "Choose whether AFKBOT should apply safe defaults now or walk you through every permission. "
                "You can still edit the profile later."
            ),
            ru=(
                "Выберите: применить безопасные настройки сразу или пройти каждое разрешение вручную. "
                "Профиль можно изменить позже."
            ),
        ),
        options=[
            (
                "recommended",
                msg(
                    lang,
                    en="Recommended - safe defaults, fastest setup",
                    ru="Рекомендуется - безопасные настройки, самый быстрый путь",
                ),
            ),
            (
                "custom",
                msg(
                    lang,
                    en="Custom - review each permission",
                    ru="Вручную - проверить каждое разрешение",
                ),
            ),
        ],
        default=default if default in {"recommended", "custom"} else "recommended",
        lang=lang,
    )


def prompt_policy_enabled(*, default: bool, lang: PromptLanguage = PromptLanguage.EN) -> bool:
    """Prompt runtime profile policy enable/disable toggle."""

    return confirm_space(
        question=msg(
            lang,
            en=(
                "Enable safety limits for this profile? Recommended. Without them, the profile policy will not "
                "block risky tool categories."
            ),
            ru=(
                "Включить ограничения безопасности для этого профиля? Рекомендуется. Без них политика профиля "
                "не будет блокировать рискованные категории инструментов."
            ),
        ),
        default=default,
        title=msg(lang, en="Setup: Security enforcement", ru="Настройка: Применение ограничений"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def prompt_policy_preset(*, default: str, lang: PromptLanguage = PromptLanguage.EN) -> str:
    """Prompt policy preset level using single-choice selector."""

    selected_default = default if default in POLICY_PRESET_CHOICES else "medium"
    selected = select_value_dialog(
        title=msg(lang, en="Setup: Security level", ru="Настройка: Уровень безопасности"),
        text=msg(
            lang,
            en="Choose how cautious AFKBOT should be before mutating or critical actions.",
            ru="Выберите, насколько осторожно AFKBOT должен вести себя перед изменениями и критичными действиями.",
        ),
        options=[
            (
                "simple",
                msg(
                    lang,
                    en="simple - move fast, minimal confirmations",
                    ru="simple - быстрее работать, минимум подтверждений",
                ),
            ),
            (
                "medium",
                msg(
                    lang,
                    en="medium - confirm dangerous file changes",
                    ru="medium - подтверждать опасные изменения файлов",
                ),
            ),
            (
                "strict",
                msg(
                    lang,
                    en="strict - confirm every critical action",
                    ru="strict - подтверждать каждое критичное действие",
                ),
            ),
        ],
        default=selected_default,
        lang=lang,
    )
    if selected in POLICY_PRESET_CHOICES:
        return selected
    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en="Security level (simple/medium/strict)",
                    ru="Уровень безопасности (simple/medium/strict)",
                ),
                default=selected_default,
            )
        ).strip().lower()
        if value in POLICY_PRESET_CHOICES:
            return value
        typer.echo(
            msg(
                lang,
                en="Invalid security level: choose simple, medium, or strict.",
                ru="Некорректный уровень: выберите simple, medium или strict.",
            )
        )


def prompt_policy_capabilities(
    *,
    preset: str,
    lang: PromptLanguage = PromptLanguage.EN,
    exclude_values: tuple[str, ...] = (),
    default_values: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Prompt capability checkbox selection based on chosen policy preset."""

    preset_level = parse_preset_level(preset)
    excluded = {value.strip().lower() for value in exclude_values if value.strip()}
    resolved_defaults = (
        tuple(value for value in default_values if value not in excluded)
        if default_values is not None
        else tuple(
            item.value
            for item in default_capabilities_for_preset(preset_level)
            if item.value not in excluded
        )
    )
    options = [
        (capability_value(spec.id), capability_label(spec.id, lang=lang))
        for spec in list_capability_specs()
        if capability_value(spec.id) not in excluded
    ]
    return select_multi_option_dialog(
        title=msg(lang, en="Setup: Capabilities", ru="Настройка: Возможности"),
        text=msg(
            lang,
            en=(
                "Choose broad tool categories this profile may use. Channels can narrow this later, but cannot "
                "grant a capability that is disabled here."
            ),
            ru=(
                "Выберите крупные категории инструментов, которые может использовать профиль. Каналы смогут "
                "позже сузить этот список, но не смогут включить то, что запрещено здесь."
            ),
        ),
        options=options,
        default_values=resolved_defaults,
        hint_text=multi_hint(lang),
    )


def prompt_policy_network_mode(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
    allow_custom: bool = False,
) -> str:
    """Prompt one network access mode for interactive security wizard."""

    options = [
        ("unrestricted", msg(lang, en="unrestricted - any network host", ru="unrestricted - любой сетевой хост")),
        (
            "recommended",
            msg(lang, en="recommended - only known provider/service hosts", ru="recommended - только известные хосты провайдеров и сервисов"),
        ),
    ]
    if allow_custom:
        options.append(
            (
                "custom",
                msg(lang, en="custom - keep current custom host list", ru="custom - оставить текущий список хостов"),
            )
        )
    options.append(
        ("deny_all", msg(lang, en="deny_all - block network tools", ru="deny_all - запретить сетевые инструменты"))
    )
    return select_value_dialog(
        title=msg(lang, en="Setup: Network access", ru="Настройка: Доступ к сети"),
        text=msg(
            lang,
            en="Choose which external hosts this profile may reach through network tools.",
            ru="Выберите, к каким внешним хостам этот профиль может обращаться через сетевые инструменты.",
        ),
        options=options,
        default=default if default in {"unrestricted", "recommended", "custom", "deny_all"} else "recommended",
        lang=lang,
    )


def prompt_policy_file_access_mode(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt one file-tool access mode for interactive security wizard."""

    return select_value_dialog(
        title=msg(lang, en="Setup: File access", ru="Настройка: Доступ к файлам"),
        text=msg(
            lang,
            en="Choose whether the agent can read or edit local files.",
            ru="Выберите, может ли агент читать или менять локальные файлы.",
        ),
        options=[
            ("none", msg(lang, en="none - no file tools", ru="none - без файловых инструментов")),
            ("read_only", msg(lang, en="read_only - read and search files", ru="read_only - читать и искать файлы")),
            (
                "read_write",
                msg(lang, en="read_write - read, write, and edit files", ru="read_write - читать, создавать и менять файлы"),
            ),
        ],
        default=default if default in {"none", "read_only", "read_write"} else "read_write",
        lang=lang,
    )


def prompt_policy_workspace_scope_mode(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
    allow_custom: bool = False,
) -> str:
    """Prompt one workspace/file scope mode for interactive security wizard."""

    options = [
        (
            "profile_only",
            msg(lang, en="profile_only - profile files only", ru="profile_only - только файлы профиля"),
        ),
        (
            "project_only",
            msg(lang, en="project_only - current project only", ru="project_only - только текущий проект"),
        ),
        (
            "profile_and_project",
            msg(lang, en="profile_and_project - profile plus project", ru="profile_and_project - профиль плюс проект"),
        ),
        (
            "full_system",
            msg(lang, en="full_system - all local files", ru="full_system - все локальные файлы"),
        ),
    ]
    if allow_custom:
        options.append(
            (
                "custom",
                msg(lang, en="custom - keep current custom paths", ru="custom - оставить текущие пути"),
            )
        )
    resolved_default = default if default in {item[0] for item in options} else "profile_only"
    return select_value_dialog(
        title=msg(lang, en="Setup: Workspace scope", ru="Настройка: Область файлового доступа"),
        text=msg(
            lang,
            en=(
                "Choose which folders file tools may touch by default. This combines with the file access mode above."
            ),
            ru=(
                "Выберите, какие папки файловые инструменты могут затрагивать по умолчанию. Это работает вместе "
                "с режимом доступа к файлам выше."
            ),
        ),
        options=options,
        default=resolved_default,
        lang=lang,
    )


def select_value_dialog(
    *,
    title: str,
    text: str,
    options: list[tuple[str, str]],
    default: str,
    lang: PromptLanguage,
) -> str:
    """Run inline single-select dialog and fall back to default on cancel."""

    selected = run_inline_single_select(
        title=title,
        text=text,
        options=options,
        default_value=default,
        hint_text=single_hint(lang),
    )
    if selected:
        return str(selected).strip()
    return default


def capability_value(capability: PolicyCapabilityId) -> str:
    """Return stable string value for one policy capability."""

    return capability.value


def capability_label(capability: PolicyCapabilityId, *, lang: PromptLanguage) -> str:
    """Return localized description for one policy capability."""

    if lang == PromptLanguage.RU:
        labels = {
            PolicyCapabilityId.FILES: "Файлы: чтение, запись, поиск и редактирование файлов",
            PolicyCapabilityId.SHELL: "Shell: выполнение команд терминала",
            PolicyCapabilityId.MEMORY: "Память: поиск и сохранение памяти профиля",
            PolicyCapabilityId.CREDENTIALS: "Секреты: управление зашифрованными credentials",
            PolicyCapabilityId.SUBAGENTS: "Субагенты: запуск и ожидание подагентов",
            PolicyCapabilityId.AUTOMATION: "Автоматизации: cron и webhook сценарии",
            PolicyCapabilityId.TASKFLOW: "Task Flow: backlog задач, зависимости и ручные назначения",
            PolicyCapabilityId.HTTP: "HTTP: исходящие HTTP-запросы",
            PolicyCapabilityId.WEB: "Web: поиск и чтение веб-страниц",
            PolicyCapabilityId.BROWSER: "Browser: управление браузером",
            PolicyCapabilityId.SKILLS: "Skills: управление навыками профиля",
            PolicyCapabilityId.APPS: "Apps: Telegram, IMAP, SMTP и другие app-интеграции",
            PolicyCapabilityId.MCP: "MCP: настройка MCP профиля и доступ к runtime MCP серверам",
            PolicyCapabilityId.EMAIL: "Email: legacy alias для Apps",
            PolicyCapabilityId.TELEGRAM: "Telegram: legacy alias для Apps",
            PolicyCapabilityId.DEBUG: "Debug: диагностические инструменты",
        }
        return labels[capability]
    labels = {
        PolicyCapabilityId.FILES: "Files: read, write, search, and edit files",
        PolicyCapabilityId.SHELL: "Shell: execute terminal commands",
        PolicyCapabilityId.MEMORY: "Memory: search and store profile memory",
        PolicyCapabilityId.CREDENTIALS: "Credentials: manage encrypted bindings",
        PolicyCapabilityId.SUBAGENTS: "Subagents: run and wait for subagents",
        PolicyCapabilityId.AUTOMATION: "Automations: cron and webhook flows",
        PolicyCapabilityId.TASKFLOW: "Task Flow: durable tasks, dependencies, and assignments",
        PolicyCapabilityId.HTTP: "HTTP: outbound HTTP requests",
        PolicyCapabilityId.WEB: "Web: search and fetch web pages",
        PolicyCapabilityId.BROWSER: "Browser: browser control actions",
        PolicyCapabilityId.SKILLS: "Skills: manage profile skills",
        PolicyCapabilityId.APPS: "Apps: Telegram, IMAP, SMTP, and app integrations",
        PolicyCapabilityId.MCP: "MCP: manage profile MCP config and access runtime MCP servers",
        PolicyCapabilityId.EMAIL: "Email: legacy alias for Apps",
        PolicyCapabilityId.TELEGRAM: "Telegram: legacy alias for Apps",
        PolicyCapabilityId.DEBUG: "Debug: diagnostics-only tools",
    }
    return labels[capability]
