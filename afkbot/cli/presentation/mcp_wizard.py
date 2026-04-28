"""Interactive helpers for MCP add-by-URL CLI flows."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.inline_select import confirm_space, run_inline_multi_select, select_option_dialog
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, multi_hint, single_hint
from afkbot.cli.presentation.tty import supports_interactive_tty
from afkbot.services.mcp_integration.operator_contracts import MCP_CONFIG_BOUNDARY_NOTE
from afkbot.services.mcp_integration.url_resolver import MCPURLResolution, resolve_mcp_url

_REMOTE_TRANSPORT_OPTIONS = ["http", "sse", "websocket"]


def mcp_wizard_enabled() -> bool:
    """Return whether interactive MCP wizard prompts can run in this terminal."""

    return supports_interactive_tty()


def prompt_mcp_url(
    *,
    default: str | None = None,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt for the remote MCP endpoint URL."""

    prompt_default = (default or "").strip() or None
    return str(
        typer.prompt(
            msg(lang, en="MCP endpoint URL", ru="URL MCP-сервера"),
            default=prompt_default,
        )
    ).strip()


def prompt_resolved_mcp_url(
    *,
    default: str | None = None,
    lang: PromptLanguage = PromptLanguage.EN,
) -> MCPURLResolution:
    """Prompt until the operator provides one valid remote MCP URL."""

    prompt_default = (default or "").strip() or None
    while True:
        candidate = prompt_mcp_url(default=prompt_default, lang=lang)
        try:
            return resolve_mcp_url(candidate)
        except ValueError as exc:
            typer.echo(msg(lang, en=f"Invalid MCP URL: {exc}", ru=f"Некорректный URL MCP-сервера: {exc}"))
            prompt_default = candidate


def prompt_mcp_server(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt for the normalized MCP server identifier."""

    return str(typer.prompt(msg(lang, en="Server id", ru="ID сервера"), default=default)).strip()


def prompt_mcp_transport(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt for the transport used by the remote MCP endpoint."""

    return select_option_dialog(
        title=msg(lang, en="MCP: Transport", ru="MCP: Транспорт"),
        text=msg(
            lang,
            en=(
                "Choose how AFKBOT connects to this remote MCP endpoint. HTTP is the normal default "
                "for URL-based servers."
            ),
            ru=(
                "Выберите, как AFKBOT подключается к этому удалённому MCP-серверу. HTTP обычно подходит "
                "для серверов с URL."
            ),
        ),
        options=list(_REMOTE_TRANSPORT_OPTIONS),
        default=default,
        hint_text=single_hint(lang),
    )


def prompt_mcp_capabilities(
    *,
    defaults: tuple[str, ...],
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, ...]:
    """Prompt for MCP capability visibility advertised to the IDE."""

    selected = run_inline_multi_select(
        title=msg(lang, en="MCP: Capabilities", ru="MCP: Возможности"),
        text=msg(
            lang,
            en=(
                "Select what this MCP server exposes to the profile. Most agent integrations need `tools`; "
                "resources and prompts are optional when the server provides them."
            ),
            ru=(
                "Выберите, что этот MCP-сервер отдаёт профилю. Для большинства интеграций агента нужны "
                "`tools`; `resources` и `prompts` включайте, если сервер их реально предоставляет."
            ),
        ),
        options=[
            ("tools", msg(lang, en="tools - callable actions", ru="tools - вызываемые действия")),
            ("resources", msg(lang, en="resources - readable context items", ru="resources - читаемый контекст")),
            ("prompts", msg(lang, en="prompts - reusable prompt templates", ru="prompts - шаблоны запросов")),
        ],
        default_values=defaults or ("tools",),
        hint_text=multi_hint(lang),
    )
    if not selected:
        return defaults or ("tools",)
    return tuple(selected)


def prompt_optional_refs(
    *,
    label: str,
    suggestion: str,
    default_values: tuple[str, ...] = (),
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, ...]:
    """Prompt for optional env/secret refs as a comma-separated list."""

    default_text = ", ".join(default_values)
    answer = str(
        typer.prompt(
            msg(
                lang,
                en=f"{label} (comma-separated, optional; example: {suggestion})",
                ru=f"{label} (через запятую, необязательно; пример: {suggestion})",
            ),
            default=default_text,
            show_default=bool(default_text),
        )
    ).strip()
    if not answer:
        return ()
    return tuple(_split_refs(answer))


def confirm_mcp_add(
    *,
    preview_text: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> bool:
    """Confirm one MCP add/update after showing the preview block."""

    return confirm_space(
        question=preview_text,
        default=True,
        title=msg(lang, en="MCP: Save config", ru="MCP: Сохранить конфигурацию"),
        yes_label=msg(lang, en="Write config", ru="Записать конфигурацию"),
        no_label=msg(lang, en="Cancel", ru="Отмена"),
        hint_text=MCP_CONFIG_BOUNDARY_NOTE,
    )


def confirm_mcp_remove(
    *,
    preview_text: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> bool:
    """Confirm one MCP removal after showing the preview block."""

    return confirm_space(
        question=preview_text,
        default=False,
        title=msg(lang, en="MCP: Remove config", ru="MCP: Удалить конфигурацию"),
        yes_label=msg(lang, en="Remove config", ru="Удалить конфигурацию"),
        no_label=msg(lang, en="Cancel", ru="Отмена"),
        hint_text=msg(
            lang,
            en="This only updates IDE-side MCP profile config.",
            ru="Это меняет только MCP-конфигурацию текущего профиля для IDE/агента.",
        ),
    )


def render_mcp_add_preview(
    *,
    resolution: MCPURLResolution,
    preview_server_id: str,
    preview_transport: str,
    preview_capabilities: tuple[str, ...],
    preview_env_refs: tuple[str, ...],
    preview_secret_refs: tuple[str, ...],
    target_path: str,
    storage_mode: str,
    replacing_existing: bool,
    enabled: bool,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Render one human-readable preview before persisting an MCP config."""

    lines = [
        msg(
            lang,
            en="Write this remote MCP server config to the current profile?",
            ru="Записать конфигурацию этого удалённого MCP-сервера в текущий профиль?",
        ),
        "",
        f"- url: {resolution.url}",
        f"- server: {preview_server_id}",
        f"- transport: {preview_transport}",
        f"- capabilities: {', '.join(preview_capabilities) or 'none'}",
        f"- env_refs: {', '.join(preview_env_refs) or 'none'}",
        f"- secret_refs: {', '.join(preview_secret_refs) or 'none'}",
        f"- enabled: {'yes' if enabled else 'no'}",
        f"- storage_mode: {storage_mode}",
        f"- target_path: {target_path}",
        f"- replace_effective_server: {'yes' if replacing_existing else 'no'}",
        f"- boundary: {MCP_CONFIG_BOUNDARY_NOTE}",
    ]
    return "\n".join(lines)


def render_mcp_remove_preview(
    *,
    server: str,
    transport: str,
    url: str | None,
    target_path: str,
    storage_mode: str,
    config_source: str | None,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Render one human-readable preview before removing an MCP config."""

    lines = [
        msg(
            lang,
            en="Remove this MCP server config from the current profile?",
            ru="Удалить конфигурацию этого MCP-сервера из текущего профиля?",
        ),
        "",
        f"- server: {server}",
        f"- transport: {transport}",
        f"- url: {url or '-'}",
        f"- storage_mode: {storage_mode}",
        f"- target_path: {target_path}",
        f"- config_source: {config_source or '-'}",
        f"- boundary: {MCP_CONFIG_BOUNDARY_NOTE}",
    ]
    return "\n".join(lines)


def _split_refs(answer: str) -> list[str]:
    return [item.strip() for item in answer.split(",") if item.strip()]
