"""Wizard preview builders."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.wizard.channel_catalog import ChannelWizardScenario, channel_scenario
from afkbot.services.wizard.contracts import WizardPreview
from afkbot.services.wizard.profile_catalog import ProfileWizardScenario, profile_scenario


def build_profile_preview(
    *,
    scenario: ProfileWizardScenario,
    allowed_directories: tuple[str, ...],
    network_allowlist: tuple[str, ...],
    lang: PromptLanguage,
) -> WizardPreview:
    """Build a localized profile ceiling preview."""

    if lang == PromptLanguage.RU:
        lines = [
            f"Потолок профиля: {scenario.label_ru} ({scenario.id})",
            f"- capabilities: {', '.join(scenario.capabilities) or '-'}",
            f"- files: {scenario.file_access_mode}, область: {scenario.workspace_scope_mode}",
            "- файлы ограничены: только директории "
            + (", ".join(allowed_directories) if allowed_directories else "профиля"),
            f"- Shell sandbox: {scenario.shell_sandbox_mode}",
            "- shell commands: "
            + (", ".join(scenario.default_shell_allowed_commands) if scenario.default_shell_allowed_commands else "-"),
            "- network: " + (", ".join(network_allowlist) if network_allowlist else scenario.network_mode),
        ]
        if scenario.shell_sandbox_mode == "best_effort":
            lines.append("- warning: best_effort не является жёсткой изоляцией без backend")
        if scenario.workspace_scope_mode == "full_system":
            lines.append("- warning: full_system открывает все локальные файлы профилю")
        return WizardPreview(lines=tuple(lines))

    lines = [
        f"Profile ceiling: {scenario.label_en} ({scenario.id})",
        f"- capabilities: {', '.join(scenario.capabilities) or '-'}",
        f"- files: {scenario.file_access_mode}, scope: {scenario.workspace_scope_mode}",
        "- files are limited to directories: "
        + (", ".join(allowed_directories) if allowed_directories else "profile workspace"),
        f"- Shell sandbox: {scenario.shell_sandbox_mode}",
        "- shell commands: "
        + (", ".join(scenario.default_shell_allowed_commands) if scenario.default_shell_allowed_commands else "-"),
        "- network: " + (", ".join(network_allowlist) if network_allowlist else scenario.network_mode),
    ]
    if scenario.shell_sandbox_mode == "best_effort":
        lines.append("- warning: best_effort is not hard isolation without a backend")
    if scenario.workspace_scope_mode == "full_system":
        lines.append("- warning: full_system exposes all local files to the profile")
    return WizardPreview(lines=tuple(lines))


def build_profile_configuration_preview(
    *,
    scenario_id: str,
    capabilities: tuple[str, ...],
    file_access_mode: str,
    workspace_scope_mode: str,
    allowed_directories: tuple[str, ...],
    shell_sandbox_mode: str,
    shell_allowed_commands: tuple[str, ...],
    network_mode: str,
    network_allowlist: tuple[str, ...],
    credential_status: tuple[str, ...],
    lang: PromptLanguage,
) -> WizardPreview:
    """Build a localized preview from effective profile policy fields."""

    scenario_label = _profile_scenario_label(scenario_id=scenario_id, lang=lang)
    capabilities_text = ", ".join(capabilities) or "-"
    directories_text = ", ".join(allowed_directories) if allowed_directories else "-"
    shell_commands_text = ", ".join(shell_allowed_commands) if shell_allowed_commands else "-"
    network_text = ", ".join(network_allowlist) if network_allowlist else network_mode
    credentials_text = ", ".join(credential_status) if credential_status else "-"
    lines = (
        (
            f"Profile preview: {scenario_label}",
            f"- credentials: {credentials_text}",
            f"- capabilities: {capabilities_text}",
            f"- files: {file_access_mode}, scope={workspace_scope_mode}, dirs={directories_text}",
            f"- shell sandbox: {shell_sandbox_mode}, commands={shell_commands_text}",
            f"- network: {network_text}",
        )
        if lang != PromptLanguage.RU
        else (
            f"Предпросмотр профиля: {scenario_label}",
            f"- учётные данные: {credentials_text}",
            f"- возможности: {_capabilities_label(capabilities, lang=lang)}",
            f"- файлы: {_file_access_label(file_access_mode, lang=lang)}, "
            f"граница={_workspace_scope_label(workspace_scope_mode, lang=lang)}, директории={directories_text}",
            f"- терминал: {_shell_sandbox_label(shell_sandbox_mode, lang=lang)}, команды={shell_commands_text}",
            f"- сеть: {_network_label(network_mode, network_allowlist, lang=lang)}",
        )
    )
    warnings = _profile_warning_lines(
        workspace_scope_mode=workspace_scope_mode,
        shell_sandbox_mode=shell_sandbox_mode,
        lang=lang,
    )
    return WizardPreview(lines=tuple(lines) + warnings)


def build_channel_preview(
    *,
    scenario: ChannelWizardScenario,
    lang: PromptLanguage,
) -> WizardPreview:
    """Build a localized channel surface preview."""

    tools = ", ".join(scenario.current_channel_tools)
    if lang == PromptLanguage.RU:
        return WizardPreview(
            lines=(
                f"Поверхность канала: {scenario.label_ru} ({scenario.transport})",
                f"- tool_profile: {scenario.tool_profile}",
                f"- trigger: {scenario.trigger_mode or '-'}",
                f"- reply_mode: {scenario.reply_mode or '-'}",
                f"- доступ: private={scenario.private_policy}, group={scenario.group_policy}",
                f"- current-channel tools: {tools}",
                "- current-channel tools доступны только для активного endpoint, "
                "без общего app.run, shell или файлов",
            )
        )
    return WizardPreview(
        lines=(
            f"Channel surface: {scenario.label_en} ({scenario.transport})",
            f"- tool_profile: {scenario.tool_profile}",
            f"- trigger: {scenario.trigger_mode or '-'}",
            f"- reply_mode: {scenario.reply_mode or '-'}",
            f"- access: private={scenario.private_policy}, group={scenario.group_policy}",
            f"- current-channel tools: {tools}",
            "- current-channel tools are scoped to the active endpoint only, "
            "without generic app.run, shell, or filesystem access",
        )
    )


def build_channel_surface_preview(
    *,
    transport: str,
    scenario_id: str | None,
    tool_profile: str,
    trigger_mode: str | None,
    reply_mode: str | None,
    private_policy: str,
    group_policy: str,
    current_channel_tools: tuple[str, ...],
    credential_status: tuple[str, ...],
    lang: PromptLanguage,
) -> WizardPreview:
    """Build a localized preview from effective channel fields."""

    scenario_label = _channel_scenario_label(scenario_id=scenario_id, lang=lang)
    tools = ", ".join(current_channel_tools) if current_channel_tools else "-"
    credentials = ", ".join(credential_status) if credential_status else "-"
    if lang == PromptLanguage.RU:
        return WizardPreview(
            lines=(
                f"Предпросмотр канала: {scenario_label} ({transport})",
                f"- учётные данные: {credentials}",
                f"- tool_profile: {tool_profile}",
                f"- trigger: {trigger_mode or '-'}",
                f"- reply_mode: {reply_mode or '-'}",
                f"- доступ: private={private_policy}, group={group_policy}",
                f"- channel-owned tools: {tools}",
                "- channel-owned tools доступны только для активного endpoint и всё равно проходят "
                "deny/network/runtime guardrails профиля.",
            )
        )
    return WizardPreview(
        lines=(
            f"Channel preview: {scenario_label} ({transport})",
            f"- credentials: {credentials}",
            f"- tool_profile: {tool_profile}",
            f"- trigger: {trigger_mode or '-'}",
            f"- reply_mode: {reply_mode or '-'}",
            f"- access: private={private_policy}, group={group_policy}",
            f"- channel-owned tools: {tools}",
            "- channel-owned tools are scoped to the active endpoint and still pass profile "
            "deny/network/runtime guardrails.",
        )
    )


def current_channel_tool_names_for_transport(transport: str) -> tuple[str, ...]:
    """Return channel-owned tools that may be injected for active turns."""

    normalized = transport.strip().lower()
    if normalized == "partyflow":
        return ("channel.history.list", "channel.send")
    if normalized in {"telegram", "telegram_user"}:
        return ("channel.send",)
    return ()


def _profile_scenario_label(*, scenario_id: str, lang: PromptLanguage) -> str:
    normalized = scenario_id.strip() or "custom"
    if normalized == "custom":
        return "Вручную (custom)" if lang == PromptLanguage.RU else "Custom"
    try:
        scenario = profile_scenario(normalized)
    except ValueError:
        return normalized
    return scenario.label_ru if lang == PromptLanguage.RU else scenario.label_en


def _channel_scenario_label(*, scenario_id: str | None, lang: PromptLanguage) -> str:
    normalized = (scenario_id or "custom").strip() or "custom"
    if normalized == "custom":
        return "Вручную (custom)" if lang == PromptLanguage.RU else "Custom"
    try:
        scenario = channel_scenario(normalized)
    except ValueError:
        return normalized
    return scenario.label_ru if lang == PromptLanguage.RU else scenario.label_en


def _profile_warning_lines(
    *,
    workspace_scope_mode: str,
    shell_sandbox_mode: str,
    lang: PromptLanguage,
) -> tuple[str, ...]:
    lines: list[str] = []
    if shell_sandbox_mode == "best_effort":
        lines.append(
            "- warning: best_effort is not hard isolation without an OS sandbox backend"
            if lang != PromptLanguage.RU
            else "- предупреждение: best_effort не является жёсткой изоляцией без OS sandbox backend"
        )
    if shell_sandbox_mode == "required":
        lines.append(
            "- shell will fail closed if the OS sandbox backend is unavailable"
            if lang != PromptLanguage.RU
            else "- shell безопасно завершится ошибкой, если OS sandbox backend недоступен"
        )
    if workspace_scope_mode == "full_system":
        lines.append(
            "- warning: full_system exposes all local files to the profile"
            if lang != PromptLanguage.RU
            else "- предупреждение: full_system открывает профилю все локальные файлы"
        )
    return tuple(lines)


def _capabilities_label(capabilities: tuple[str, ...], *, lang: PromptLanguage) -> str:
    if lang != PromptLanguage.RU:
        return ", ".join(capabilities) or "-"
    labels = {
        "files": "файлы",
        "shell": "терминал",
        "memory": "память",
        "credentials": "секреты",
        "subagents": "помощники",
        "automation": "автоматизации",
        "taskflow": "задачи",
        "http": "исходящие запросы",
        "web": "веб-страницы",
        "browser": "браузер",
        "skills": "навыки",
        "apps": "интеграции",
        "mcp": "MCP",
    }
    return ", ".join(labels.get(item, item) for item in capabilities) or "-"


def _file_access_label(value: str, *, lang: PromptLanguage) -> str:
    if lang != PromptLanguage.RU:
        return value
    return {
        "none": "без доступа",
        "read_only": "только чтение",
        "read_write": "чтение и изменение",
    }.get(value, value)


def _workspace_scope_label(value: str, *, lang: PromptLanguage) -> str:
    if lang != PromptLanguage.RU:
        return value
    return {
        "profile_only": "только папка профиля",
        "project_only": "только текущий проект",
        "profile_and_project": "папка профиля и текущий проект",
        "full_system": "вся локальная система",
        "custom": "заданные директории",
    }.get(value, value)


def _shell_sandbox_label(value: str, *, lang: PromptLanguage) -> str:
    if lang != PromptLanguage.RU:
        return value
    return {
        "disabled": "запрещён или не нужен",
        "required": "обязательная системная изоляция",
        "best_effort": "изоляция при наличии backend",
    }.get(value, value)


def _network_label(mode: str, allowlist: tuple[str, ...], *, lang: PromptLanguage) -> str:
    if lang != PromptLanguage.RU:
        return ", ".join(allowlist) if allowlist else mode
    if allowlist:
        return ", ".join(allowlist)
    return {
        "deny_all": "без сетевых инструментов",
        "recommended": "только нужные домены сервисов",
        "unrestricted": "любые сетевые адреса",
        "custom": "заданные домены",
    }.get(mode, mode)
