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
            en="Choose the quick recommended safety setup or review permissions yourself.",
            ru="Выберите быстрый рекомендуемый вариант безопасности или настройте права вручную.",
        ),
        options=[
            (
                "recommended",
                msg(
                    lang,
                    en="Recommended: safe defaults",
                    ru="Рекомендуется: безопасные настройки",
                ),
            ),
            (
                "custom",
                msg(
                    lang,
                    en="Custom: review each permission",
                    ru="Вручную: проверить каждое право",
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
            en="Enable safety limits now? Recommended.",
            ru="Включить ограничения безопасности сейчас? Рекомендуется.",
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
            en="Choose how often AFKBOT should ask before risky actions.",
            ru="Выберите, как часто AFKBOT должен спрашивать подтверждение перед рискованными действиями.",
        ),
        options=[
            (
                "simple",
                msg(
                    lang,
                    en="Simple: move fast, almost no confirmations",
                    ru="Лёгкий: быстрее работать, почти без подтверждений",
                ),
            ),
            (
                "medium",
                msg(
                    lang,
                    en="Medium: confirm dangerous file changes",
                    ru="Средний: подтверждать опасные изменения файлов",
                ),
            ),
            (
                "strict",
                msg(
                    lang,
                    en="Strict: confirm every critical action",
                    ru="Строгий: подтверждать каждое критичное действие",
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
            en="Choose what the agent is allowed to do.",
            ru="Выберите, что агенту разрешено делать.",
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
        ("unrestricted", msg(lang, en="Full network access", ru="Полный сетевой доступ")),
        (
            "recommended",
            msg(lang, en="Recommended hosts only", ru="Только рекомендованные хосты"),
        ),
    ]
    if allow_custom:
        options.append(
            (
                "custom",
                msg(lang, en="Keep current custom hosts", ru="Оставить текущие custom-хосты"),
            )
        )
    options.append(
        ("deny_all", msg(lang, en="Block all network access", ru="Запретить весь сетевой доступ"))
    )
    return select_value_dialog(
        title=msg(lang, en="Setup: Network access", ru="Настройка: Доступ к сети"),
        text=msg(
            lang,
            en="Choose how much network access the agent should have.",
            ru="Выберите, какой сетевой доступ должен быть у агента.",
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
            ("none", msg(lang, en="No file tools", ru="Без файловых инструментов")),
            ("read_only", msg(lang, en="Read only", ru="Только чтение")),
            ("read_write", msg(lang, en="Read, write, and edit", ru="Чтение, запись и редактирование")),
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
            msg(lang, en="Profile workspace only", ru="Только папка профиля"),
        ),
        (
            "project_only",
            msg(lang, en="Project workspace only", ru="Только папка проекта"),
        ),
        (
            "profile_and_project",
            msg(lang, en="Profile + project workspaces", ru="Папка профиля + папка проекта"),
        ),
        (
            "full_system",
            msg(lang, en="All local files", ru="Все локальные файлы"),
        ),
    ]
    if allow_custom:
        options.append(
            (
                "custom",
                msg(lang, en="Keep current custom paths", ru="Оставить текущие custom-пути"),
            )
        )
    resolved_default = default if default in {item[0] for item in options} else "profile_only"
    return select_value_dialog(
        title=msg(lang, en="Setup: Workspace scope", ru="Настройка: Область файлового доступа"),
        text=msg(
            lang,
            en="Choose which folders the agent should treat as its working area by default.",
            ru="Выберите, какие папки агент должен считать своей рабочей областью по умолчанию.",
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
