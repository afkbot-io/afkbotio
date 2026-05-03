"""Profile/setup wizard inventory and scenario catalog."""

from __future__ import annotations

from dataclasses import dataclass

from typing import cast

from afkbot.services.setup.contracts import NETWORK_POLICY_RECOMMENDED_HOSTS, WILDCARD_NETWORK_HOST
from afkbot.services.wizard.contracts import (
    WizardBranch,
    WizardPlan,
    WizardQuestion,
    WizardQuestionKind,
)


@dataclass(frozen=True, slots=True)
class ProfileWizardScenario:
    """User-facing profile intent mapped to stable policy fields."""

    id: str
    label_en: str
    label_ru: str
    capabilities: tuple[str, ...]
    file_access_mode: str
    workspace_scope_mode: str
    shell_sandbox_mode: str
    default_shell_allowed_commands: tuple[str, ...] = ()
    network_mode: str = "recommended"


_PROFILE_SCENARIOS: dict[str, ProfileWizardScenario] = {
    "chat_only": ProfileWizardScenario(
        id="chat_only",
        label_en="Chat only",
        label_ru="Только чат",
        capabilities=("memory",),
        file_access_mode="none",
        workspace_scope_mode="profile_only",
        shell_sandbox_mode="disabled",
    ),
    "taskflow_channel": ProfileWizardScenario(
        id="taskflow_channel",
        label_en="Channel replies and tasks",
        label_ru="Канал: ответы и задачи",
        capabilities=("memory", "taskflow"),
        file_access_mode="none",
        workspace_scope_mode="profile_only",
        shell_sandbox_mode="disabled",
    ),
    "project_readonly": ProfileWizardScenario(
        id="project_readonly",
        label_en="Read project files",
        label_ru="Читать файлы проекта",
        capabilities=("files", "memory", "web"),
        file_access_mode="read_only",
        workspace_scope_mode="project_only",
        shell_sandbox_mode="disabled",
    ),
    "sandbox_writer": ProfileWizardScenario(
        id="sandbox_writer",
        label_en="Private profile folder writer",
        label_ru="Писать только в личной папке профиля",
        capabilities=("files", "memory"),
        file_access_mode="read_write",
        workspace_scope_mode="profile_only",
        shell_sandbox_mode="disabled",
    ),
    "sandbox_shell": ProfileWizardScenario(
        id="sandbox_shell",
        label_en="Approved terminal in the private folder",
        label_ru="Разрешённый терминал в личной папке профиля",
        capabilities=("files", "shell", "memory"),
        file_access_mode="read_write",
        workspace_scope_mode="profile_only",
        shell_sandbox_mode="required",
        default_shell_allowed_commands=("ls", "cat", "pwd", "grep", "rg", "sed", "head", "tail"),
    ),
    "trusted_admin": ProfileWizardScenario(
        id="trusted_admin",
        label_en="Full system access (expert)",
        label_ru="Полный системный доступ (экспертно)",
        capabilities=(
            "files",
            "shell",
            "memory",
            "credentials",
            "subagents",
            "automation",
            "taskflow",
            "http",
            "web",
            "browser",
            "skills",
            "apps",
            "mcp",
        ),
        file_access_mode="read_write",
        workspace_scope_mode="full_system",
        shell_sandbox_mode="disabled",
        network_mode="unrestricted",
    ),
}


def profile_scenario(scenario_id: str) -> ProfileWizardScenario:
    """Return one profile wizard scenario by id."""

    try:
        return _PROFILE_SCENARIOS[scenario_id]
    except KeyError as exc:
        raise ValueError(f"Unknown profile wizard scenario: {scenario_id}") from exc


def list_profile_scenarios() -> tuple[ProfileWizardScenario, ...]:
    """Return user-facing profile wizard scenarios in stable order."""

    return tuple(_PROFILE_SCENARIOS.values())


def infer_profile_scenario_id(
    *,
    capabilities: tuple[str, ...],
    file_access_mode: str,
    workspace_scope_mode: str,
    shell_sandbox_mode: str,
    shell_allowed_commands: tuple[str, ...] = (),
    network_mode: str | None = None,
    network_allowlist: tuple[str, ...] = (),
) -> str:
    """Infer a scenario id from canonical persisted policy fields."""

    normalized_capabilities = tuple(dict.fromkeys(item.strip().lower() for item in capabilities if item.strip()))
    normalized_shell_commands = tuple(
        dict.fromkeys(item.strip().lower() for item in shell_allowed_commands if item.strip())
    )
    normalized_network_mode = None if network_mode is None else network_mode.strip().lower()
    normalized_network_allowlist = tuple(
        dict.fromkeys(item.strip().lower() for item in network_allowlist if item.strip())
    )
    for scenario in list_profile_scenarios():
        if (
            normalized_capabilities == scenario.capabilities
            and file_access_mode == scenario.file_access_mode
            and workspace_scope_mode == scenario.workspace_scope_mode
            and shell_sandbox_mode == scenario.shell_sandbox_mode
            and normalized_shell_commands == scenario.default_shell_allowed_commands
            and (
                normalized_network_mode is None
                or normalized_network_mode == scenario.network_mode
            )
            and (
                not normalized_network_allowlist
                or normalized_network_allowlist
                == _expected_network_allowlist(
                    network_mode=scenario.network_mode,
                    capabilities=scenario.capabilities,
                )
            )
        ):
            return scenario.id
    return "custom"


def setup_profile_plan() -> WizardPlan:
    """Return the current setup/profile wizard inventory."""

    return WizardPlan(
        id="setup_profile",
        title_en="Setup/Profile Wizard",
        title_ru="Мастер setup/profile",
        questions=(
            _question("security_ack", "provider", "confirm", "Setup: Security acknowledgment", "Настройка: Подтверждение безопасности"),
            _question("ai_provider", "provider", "single", "Setup: AI provider", "Настройка: AI-провайдер"),
            _question("chat_model", "provider", "single", "Setup: Chat model", "Настройка: Модель чата"),
            _question("reasoning_effort", "provider", "single", "Setup: Reasoning effort", "Настройка: Глубина рассуждения"),
            _question("custom_interface", "provider", "single", "Setup: Custom interface", "Настройка: Интерфейс своего API", shown_when="provider == custom"),
            _question("provider_credentials", "provider", "secret", "Setup: Provider credentials", "Настройка: Учетные данные провайдера"),
            _question("proxy", "provider", "confirm", "Setup: Proxy", "Настройка: Прокси"),
            _question("security_setup_mode", "security", "single", "Setup: Security setup", "Настройка: Безопасность"),
            _question("setup_depth", "security", "single", "Setup: Security setup", "Настройка: Способ настройки"),
            _question("work_contexts", "security", "multi", "Where will the bot work?", "Где будет работать бот?", shown_when="setup_depth == guided"),
            _question("actions", "security", "multi", "What may the bot do?", "Что боту можно делать?", shown_when="setup_depth == guided"),
            _question("isolation", "security", "single", "Isolation", "Изоляция", shown_when="setup_depth == guided"),
            _question("confirmation", "security", "single", "Confirmations", "Подтверждения", shown_when="setup_depth == guided"),
            _question("intent_network", "security", "single", "Network", "Сеть", shown_when="setup_depth == guided"),
            _question("security_enforcement", "security", "confirm", "Setup: Security enforcement", "Настройка: Применение ограничений", shown_when="security_setup_mode == custom"),
            _question("confirmation_mode", "security", "single", "Setup: Security level", "Настройка: Уровень безопасности", shown_when="setup_depth == expert"),
            _question("profile_scenario", "security", "single", "Legacy profile scenario", "Legacy-сценарий профиля", shown_when="legacy_only"),
            _question("capabilities", "security", "multi", "Low-level capabilities", "Низкоуровневые возможности", shown_when="setup_depth == expert"),
            _question("file_access", "security", "single", "Low-level file access", "Низкоуровневый доступ к файлам", shown_when="setup_depth == expert"),
            _question("workspace_scope", "security", "single", "Low-level workspace scope", "Низкоуровневая область файлов", shown_when="setup_depth == expert && files_enabled"),
            _question("shell_sandbox", "security", "single", "Low-level terminal sandbox", "Низкоуровневая изоляция терминала", shown_when="setup_depth == expert && shell_enabled"),
            _question("shell_allowed_commands", "security", "text", "Allowed terminal commands", "Разрешённые команды терминала", shown_when="setup_depth == expert && shell_enabled"),
            _question("shell_sandbox_backend", "security", "confirm", "Setup: Shell sandbox backend", "Настройка: Shell sandbox backend", shown_when="restricted_shell_backend_missing"),
            _question("network_access", "security", "single", "Setup: Network access", "Настройка: Доступ к сети", shown_when="policy_enabled"),
            _question("update_prompts", "runtime", "confirm", "Setup: Update prompts", "Настройка: Подсказки об обновлениях"),
            _question("runtime_host", "runtime", "text", "Runtime host", "Адрес локальной службы AFKBOT", advanced=True),
            _question("runtime_port", "runtime", "integer", "Runtime port", "Порт локальной службы AFKBOT", advanced=True),
            _question("nginx", "runtime", "confirm", "Setup: Nginx", "Настройка: Nginx", advanced=True),
            _question("https", "runtime", "confirm", "Setup: HTTPS", "Настройка: HTTPS", advanced=True),
        ),
        branches=(
            _branch(
                "guided_security",
                "security_setup_mode == custom && setup_depth == guided",
                (
                    "setup_depth",
                    "work_contexts",
                    "actions",
                    "isolation",
                    "confirmation",
                    "intent_network",
                ),
                "Guided security setup",
                "Настройка безопасности по сценариям",
            ),
            _branch(
                "expert_security",
                "security_setup_mode == custom && setup_depth == expert",
                (
                    "security_enforcement",
                    "confirmation_mode",
                    "capabilities",
                    "file_access",
                    "workspace_scope",
                    "shell_sandbox",
                    "shell_allowed_commands",
                    "network_access",
                ),
                "Expert permission review",
                "Экспертная проверка разрешений",
            ),
            _branch(
                "files_enabled",
                "files_enabled",
                ("workspace_scope",),
                "File boundary branch",
                "Ветка границ файлов",
            ),
            _branch(
                "shell_enabled",
                "shell_enabled",
                ("shell_sandbox", "shell_allowed_commands", "shell_sandbox_backend"),
                "Shell sandbox branch",
                "Ветка sandbox для shell",
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
        choices=(),
        default_value=None,
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


def _expected_network_allowlist(
    *,
    network_mode: str,
    capabilities: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = network_mode.strip().lower()
    if normalized == "unrestricted":
        return (WILDCARD_NETWORK_HOST,)
    if normalized == "deny_all":
        return ()
    if normalized == "recommended":
        hosts: list[str] = []
        seen: set[str] = set()
        for capability in capabilities:
            for host in NETWORK_POLICY_RECOMMENDED_HOSTS.get(capability, ()):
                value = host.strip().lower()
                if not value or value in seen:
                    continue
                seen.add(value)
                hosts.append(value)
        return tuple(hosts)
    return ()
