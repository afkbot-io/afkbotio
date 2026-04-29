"""Shared CLI helpers for channel endpoint mutation and configuration rendering."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import cast

import typer

from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_text,
)
from afkbot.cli.presentation.inline_select import select_option_dialog
from afkbot.cli.presentation.prompt_i18n import msg, single_hint
from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.channel_routing import ChannelBindingRule
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channel_routing.service import (
    ChannelBindingService,
    ChannelBindingServiceError,
    run_channel_binding_service_sync,
)
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessMode,
    ChannelAccessPolicy,
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
)
from afkbot.services.channels.tool_profiles import (
    CHANNEL_TOOL_PROFILE_VALUES,
    ChannelToolProfile,
    allowed_tool_names_for_channel_profile,
    default_channel_tool_profile_for_policy,
)
from afkbot.services.profile_runtime import ProfileDetails, run_profile_service_sync
from afkbot.settings import Settings


@dataclass(frozen=True)
class CollectedChannelAddBaseInputs:
    """Shared base inputs collected by one channel add flow."""

    channel_id: str
    profile_id: str
    credential_profile_key: str
    account_id: str
    enabled: bool
    tool_profile: ChannelToolProfile
    create_binding: bool
    session_policy: SessionPolicy


@dataclass(frozen=True)
class ResolvedBindingUpdateInputs:
    """Shared resolved binding inputs for one channel update flow."""

    session_policy: SessionPolicy
    priority: int
    prompt_overlay: str | None


CHANNEL_ACCESS_MODES: tuple[str, ...] = ("open", "allowlist", "disabled")


def should_collect_channel_add_interactively(
    *,
    yes: bool,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
) -> bool:
    """Return whether channel add should prompt for missing base values."""

    return not yes and (channel_id is None or profile_id is None or credential_profile_key is None)


def should_collect_channel_update_interactively(
    *,
    yes: bool,
    sync_binding: bool,
    values: tuple[object | None, ...],
) -> bool:
    """Return whether one channel update should prompt using current defaults."""

    return not yes and not sync_binding and all(value is None for value in values)


def build_generated_channel_id(*, transport: str) -> str:
    """Build one safe default channel id for interactive and `--yes` add flows."""

    normalized_transport = transport.strip().lower()
    return f"{normalized_transport}-{secrets.token_hex(4)}"


def render_channel_add_intro(
    *,
    transport: str,
    lang: PromptLanguage,
    suggested_channel_id: str,
) -> None:
    """Render one short operator-facing intro before interactive channel setup."""

    normalized_transport = transport.strip().lower()
    if normalized_transport == "telegram":
        typer.echo(
            msg(
                lang,
                en=(
                    "Telegram Bot API channel setup\n"
                    "- Use this for a Telegram bot created in @BotFather. AFKBOT will poll updates and route "
                    "allowed messages into the selected profile.\n"
                    f"- `Channel id` is only a local AFKBOT name for later `show`, `update`, `status`, and "
                    f"`poll-once` commands. Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- The profile still owns persona, memory, skills, and the maximum tool permissions. "
                    "Channel settings only narrow who may talk to it and which tools are visible from this channel.\n"
                    "- If bot credentials are not configured yet, the wizard will ask for the BotFather token. "
                    "The optional default chat id is only for app-level Telegram send actions."
                ),
                ru=(
                    "Настройка канала Telegram Bot API\n"
                    "- Используйте это для Telegram-бота, созданного в @BotFather. AFKBOT будет получать "
                    "обновления и передавать разрешённые сообщения в выбранный профиль.\n"
                    f"- `Идентификатор канала` это только локальное имя внутри AFKBOT для команд `show`, "
                    f"`update`, `status` и `poll-once`. На этом вопросе можно нажать Enter и принять "
                    f"`{suggested_channel_id}`.\n"
                    "- Профиль по-прежнему задаёт роль агента, память, навыки и максимальные права на инструменты. "
                    "Настройки канала только сужают, кто может писать и какие инструменты видны из этого канала.\n"
                    "- Если данные бота ещё не настроены, мастер попросит токен BotFather. Необязательный chat id "
                    "по умолчанию нужен только для app-level отправки сообщений в Telegram."
                ),
            )
        )
        return
    if normalized_transport == "telethon":
        typer.echo(
            msg(
                lang,
                en=(
                    "Telethon user-account channel setup\n"
                    "- Use this when AFKBOT should read Telegram as a user account instead of a bot. "
                    "It can run as read-only, reply back to chats, or collect watcher digests.\n"
                    f"- `Channel id` is only a local AFKBOT name for later `show`, `update`, `status`, "
                    f"`authorize`, and `dialogs` commands. Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- The profile still owns persona, memory, skills, and the maximum tool permissions. "
                    "Channel settings only narrow who may talk to it and which tools are visible from this channel.\n"
                    "- If Telethon credentials are not configured yet, the wizard will ask for API id, API hash, "
                    "and phone. Session string is optional: import it now or authorize later."
                ),
                ru=(
                    "Настройка канала Telegram user-аккаунта через Telethon\n"
                    "- Используйте это, когда AFKBOT должен читать Telegram как пользователь, а не как бот. "
                    "Канал может быть только для чтения, отвечать в чаты или собирать дайджесты наблюдателя.\n"
                    f"- `Идентификатор канала` это только локальное имя внутри AFKBOT для команд `show`, "
                    f"`update`, `status`, `authorize` и `dialogs`. На этом вопросе можно нажать Enter и принять "
                    f"`{suggested_channel_id}`.\n"
                    "- Профиль по-прежнему задаёт роль агента, память, навыки и максимальные права на инструменты. "
                    "Настройки канала только сужают, кто может писать и какие инструменты видны из этого канала.\n"
                    "- Если данные Telethon ещё не настроены, мастер попросит API id, API hash и телефон. "
                    "Session string необязателен: его можно импортировать сейчас или авторизоваться позже."
                ),
            )
        )
        return
    if normalized_transport == "partyflow":
        typer.echo(
            msg(
                lang,
                en=(
                    "PartyFlow webhook channel setup\n"
                    f"- This wizard creates one PartyFlow outgoing-webhook endpoint.\n"
                    f"- `Channel id` is your local AFKBOT id for later `show`, `update`, and `delete` commands. "
                    f"Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- PartyFlow does not support Telegram-style polling here; webhook is the only ingress mode.\n"
                    "- You will need the bot token and the webhook signing secret from PartyFlow UI."
                ),
                ru=(
                    "Настройка PartyFlow webhook-канала\n"
                    f"- Этот мастер создаёт один endpoint для исходящих webhook-событий PartyFlow.\n"
                    f"- `Идентификатор канала` это локальный id внутри AFKBOT для команд `show`, `update` и `delete`. "
                    f"На этом вопросе можно просто нажать Enter и принять `{suggested_channel_id}`.\n"
                    "- Polling в стиле Telegram здесь не поддерживается; сейчас доступен только режим webhook.\n"
                    "- Понадобятся токен бота и секрет подписи webhook из UI PartyFlow."
                ),
            )
        )
        return
    raise ValueError(f"Unsupported channel transport for intro: {transport}")


def collect_channel_add_base_inputs(
    *,
    settings: Settings,
    interactive: bool,
    lang: PromptLanguage,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
    enabled: bool | None,
    tool_profile: str | None,
    create_binding: bool | None,
    session_policy: SessionPolicy | None,
    binding_session_policy_default: SessionPolicy,
    binding_session_policy_allowed: tuple[str, ...],
    generated_channel_id: str,
) -> CollectedChannelAddBaseInputs:
    """Collect shared channel add inputs for any transport family."""

    resolved_channel_id = resolve_channel_text(
        value=channel_id,
        interactive=interactive,
        prompt_en="Channel id",
        prompt_ru="Идентификатор канала",
        default=(generated_channel_id if channel_id is None else None),
        lang=lang,
        normalize_lower=True,
        detail_en=(
            "This is AFKBOT's stable local id for the channel. "
            "It is used later in `afk channel show`, `update`, `status`, and runtime commands. "
            f"Allowed format: lowercase letters, digits, hyphen. Press Enter to accept `{generated_channel_id}`."
        ),
        detail_ru=(
            "Это стабильный локальный id канала внутри AFKBOT. "
            "Он потом используется в `afk channel show`, `update`, `status` и runtime-командах. "
            f"Допустимы строчные буквы, цифры и дефис. Нажмите Enter, чтобы принять `{generated_channel_id}`."
        ),
    )
    resolved_profile_id = resolve_channel_profile_id(
        settings=settings,
        profile_id=profile_id,
        interactive=interactive,
        default="default",
        lang=lang,
    )
    profile = load_channel_profile(settings=settings, profile_id=resolved_profile_id)
    default_tool_profile = default_channel_tool_profile_for_policy(policy=profile.policy)
    resolved_credential_profile_key = normalize_channel_choice_value(
        credential_profile_key or resolved_channel_id
    )
    resolved_account_id = resolve_channel_text(
        value=account_id,
        interactive=False,
        prompt_en="Account id",
        prompt_ru="ID аккаунта",
        default=resolved_channel_id,
        lang=lang,
        normalize_lower=True,
    )
    resolved_enabled = resolve_channel_bool(
        value=enabled,
        interactive=interactive,
        prompt_en="Enable channel?",
        prompt_ru="Включить канал?",
        default=True,
        lang=lang,
        detail_en="Disabled channels stay saved in config, but AFKBOT will not start or poll them until enabled.",
        detail_ru=(
            "Отключённый канал останется в конфиге, но AFKBOT не будет его запускать "
            "и опрашивать, пока вы снова не включите канал."
        ),
    )
    resolved_tool_profile = normalize_channel_tool_profile(
        resolve_channel_choice(
            value=tool_profile,
            interactive=interactive,
            prompt_en="Channel tool profile",
            prompt_ru="Профиль инструментов канала",
            default=default_tool_profile,
            allowed=CHANNEL_TOOL_PROFILE_VALUES,
            lang=lang,
            detail_en=(
                "Choose the tool set visible from this channel. This cannot grant more than the profile allows; "
                "it only narrows the profile ceiling. For private admin support use `support_readonly`. "
                "Use `inherit` only for a fully trusted channel."
            ),
            detail_ru=(
                "Выберите набор инструментов, видимый из этого канала. Это не может дать больше прав, "
                "чем разрешает профиль; настройка только сужает потолок профиля. Для личного support/admin-бота "
                "обычно подходит `support_readonly`. `inherit` выбирайте только для полностью доверенного канала."
            ),
        )
    )
    resolved_create_binding = resolve_channel_bool(
        value=create_binding,
        interactive=interactive,
        prompt_en="Create matching routing binding?",
        prompt_ru="Создать привязку маршрутизации?",
        default=True,
        lang=lang,
        detail_en=(
            "A binding connects inbound channel events to this profile and defines how sessions are grouped. "
            "Leave this on for normal setup. Turn it off only if you plan to manage channel routing manually."
        ),
        detail_ru=(
            "Привязка маршрутизации соединяет входящие события канала с этим профилем и задаёт, "
            "как сообщения группируются в диалоги. Для обычной настройки оставьте включённым. "
            "Отключайте только если будете настраивать маршрутизацию вручную."
        ),
    )
    resolved_session_policy: SessionPolicy = (
        normalize_session_policy(
            resolve_channel_choice(
                value=session_policy,
                interactive=interactive,
                prompt_en="Binding session policy",
                prompt_ru="Как группировать диалоги",
                default=binding_session_policy_default,
                allowed=binding_session_policy_allowed,
                lang=lang,
                detail_en=(
                    "Choose how AFKBOT groups incoming messages into conversations. Use `per-chat` for a private "
                    "1:1 bot, `per-thread` for topic-based groups, and `per-user-in-group` when each group member "
                    "needs a separate context."
                ),
                detail_ru=(
                    "Выберите, как AFKBOT объединяет входящие сообщения в диалоги. Для личного чата 1 на 1 "
                    "обычно нужен `per-chat`, для групп с темами - `per-thread`, а для отдельного контекста "
                    "каждого участника группы - `per-user-in-group`."
                ),
            )
        )
        if resolved_create_binding
        else binding_session_policy_default
    )
    return CollectedChannelAddBaseInputs(
        channel_id=resolved_channel_id,
        profile_id=resolved_profile_id,
        credential_profile_key=resolved_credential_profile_key,
        account_id=resolved_account_id,
        enabled=resolved_enabled,
        tool_profile=resolved_tool_profile,
        create_binding=resolved_create_binding,
        session_policy=resolved_session_policy,
    )


def load_channel_profile(*, settings: Settings, profile_id: str) -> ProfileDetails:
    """Load one profile for channel CLI flows and fail if it does not exist."""

    return run_profile_service_sync(settings, lambda service: service.get(profile_id=profile_id))


def list_channel_profiles(*, settings: Settings) -> tuple[str, ...]:
    """List profile ids available to channel interactive flows."""

    profiles = run_profile_service_sync(settings, lambda service: service.list())
    return tuple(item.id for item in profiles)


def resolve_channel_profile_id(
    *,
    settings: Settings,
    profile_id: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve one profile id using selector-first UX with text fallback."""

    if profile_id is not None:
        return normalize_channel_choice_value(profile_id)
    available = list_channel_profiles(settings=settings)
    if interactive and available:
        selected = select_option_dialog(
            title=msg(lang, en="Channel: Profile", ru="Канал: Профиль"),
            text=msg(
                lang,
                en="Select the profile that will own this channel. The profile provides the agent identity, runtime defaults, memory rules, and maximum permissions.",
                ru="Выберите профиль, к которому будет привязан этот канал. Профиль задаёт роль агента, настройки запуска, правила памяти и максимальные права.",
            ),
            options=list(available),
            default=(default if default in available else available[0]),
            hint_text=single_hint(lang),
        )
        return normalize_channel_choice_value(selected)
    return resolve_channel_text(
        value=profile_id,
        interactive=interactive,
        prompt_en="Profile id",
        prompt_ru="Идентификатор профиля",
        default=default,
        lang=lang,
        normalize_lower=True,
    )


def put_matching_binding(
    *,
    settings: Settings,
    binding_id: str,
    transport: str,
    profile_id: str,
    session_policy: SessionPolicy,
    priority: int,
    enabled: bool,
    account_id: str,
    prompt_overlay: str | None,
) -> None:
    """Create or update one matching binding for a channel endpoint."""

    run_channel_binding_service_sync(
        settings,
        lambda service: service.put(
            ChannelBindingRule(
                binding_id=binding_id,
                transport=transport,
                profile_id=profile_id,
                session_policy=session_policy,
                priority=priority,
                enabled=enabled,
                account_id=account_id,
                prompt_overlay=prompt_overlay,
            )
        ),
    )


def collect_channel_access_policy_inputs(
    *,
    interactive: bool,
    lang: PromptLanguage,
    private_policy: str | None,
    allow_from: str | None,
    group_policy: str | None,
    groups: str | None,
    group_allow_from: str | None,
    outbound_allow_to: str | None = None,
    tool_profile: ChannelToolProfile | None = None,
    private_policy_default: ChannelAccessMode = "open",
    allow_from_default: tuple[str, ...] = (),
    group_policy_default: ChannelAccessMode = "open",
    groups_default: tuple[str, ...] = (),
    group_allow_from_default: tuple[str, ...] = (),
    outbound_allow_to_default: tuple[str, ...] = (),
) -> ChannelAccessPolicy:
    """Collect OpenClaw-style access controls shared by channel transports."""

    normalized_allow_from = (
        split_channel_access_list(allow_from) if allow_from is not None else allow_from_default
    )
    normalized_groups = split_channel_access_list(groups) if groups is not None else groups_default
    normalized_group_allow_from = (
        split_channel_access_list(group_allow_from)
        if group_allow_from is not None
        else group_allow_from_default
    )
    normalized_outbound_allow_to = (
        split_channel_access_list(outbound_allow_to)
        if outbound_allow_to is not None
        else outbound_allow_to_default
    )
    private_policy_value = private_policy
    if (
        private_policy_value is None
        and not interactive
        and private_policy_default == "open"
        and allow_from is not None
    ):
        private_policy_value = "allowlist" if normalized_allow_from else None
    group_policy_value = group_policy
    if group_policy_value is None and not interactive and group_policy_default == "open":
        if groups is not None or group_allow_from is not None:
            group_policy_value = (
                "allowlist" if normalized_groups or normalized_group_allow_from else None
            )
    resolved_private_policy = cast(
        ChannelAccessMode,
        resolve_channel_choice(
            value=private_policy_value,
            interactive=interactive,
            prompt_en="Private chat access",
            prompt_ru="Доступ в личных чатах",
            default=private_policy_default,
            allowed=CHANNEL_ACCESS_MODES,
            lang=lang,
            detail_en=(
                "Choose who may write to the bot/userbot in direct messages. For a private 1:1 remote assistant, "
                "choose `allowlist` and enter the platform sender/user id next."
            ),
            detail_ru=(
                "Выберите, кто может писать боту или userbot в личные сообщения. Для закрытого личного "
                "удалённого помощника выберите `allowlist` и дальше введите id отправителя/пользователя."
            ),
        ),
    )
    if resolved_private_policy == "allowlist" and (interactive or not normalized_allow_from):
        normalized_allow_from = split_channel_access_list(
            resolve_channel_text(
                value=allow_from if allow_from is not None else None,
                interactive=interactive,
                prompt_en="Allowed private sender ids",
                prompt_ru="ID отправителей для личных чатов",
                default=", ".join(normalized_allow_from) if normalized_allow_from else None,
                lang=lang,
                detail_en=(
                    "Enter platform sender/user IDs allowed to write in private chats. Separate several IDs "
                    "with commas."
                ),
                detail_ru=(
                    "Введите id отправителей/пользователей, которым разрешено писать в личку. Несколько ID "
                    "разделяйте запятыми."
                ),
            )
        )
    resolved_group_policy = cast(
        ChannelAccessMode,
        resolve_channel_choice(
            value=group_policy_value,
            interactive=interactive,
            prompt_en="Group access",
            prompt_ru="Доступ в группах",
            default=group_policy_default,
            allowed=CHANNEL_ACCESS_MODES,
            lang=lang,
            detail_en=(
                "Choose whether this channel works in groups. `allowlist` is safest: only listed groups and listed "
                "senders inside those groups can trigger the bot."
            ),
            detail_ru=(
                "Выберите, работает ли канал в группах. Самый безопасный вариант - `allowlist`: запускать бота "
                "смогут только указанные группы и указанные отправители внутри этих групп."
            ),
        ),
    )
    if resolved_group_policy == "allowlist" and (interactive or not normalized_groups):
        normalized_groups = split_channel_access_list(
            resolve_channel_text(
                value=groups if groups is not None else None,
                interactive=interactive,
                prompt_en="Allowed group ids",
                prompt_ru="ID групп/каналов",
                default=", ".join(normalized_groups) if normalized_groups else None,
                lang=lang,
                detail_en=(
                    "Enter platform group/channel IDs allowed to use this channel. Separate several IDs with commas."
                ),
                detail_ru=(
                    "Введите id групп или каналов, где разрешён этот канал. Несколько ID разделяйте запятыми."
                ),
            )
        )
    if resolved_group_policy == "allowlist" and (
        interactive or not (normalized_group_allow_from or normalized_allow_from)
    ):
        normalized_group_allow_from = split_channel_access_list(
            resolve_channel_text(
                value=group_allow_from if group_allow_from is not None else None,
                interactive=interactive,
                prompt_en="Allowed group sender ids",
                prompt_ru="ID отправителей в группах",
                default=(
                    ", ".join(normalized_group_allow_from or normalized_allow_from)
                    if normalized_group_allow_from or normalized_allow_from
                    else None
                ),
                lang=lang,
                detail_en=(
                    "Enter platform sender/user IDs allowed to trigger the bot inside the allowed groups. "
                    "Leave no broad group access unless you trust every member."
                ),
                detail_ru=(
                    "Введите id отправителей/пользователей, которым разрешено запускать бота в разрешённых группах. "
                    "Не оставляйте широкий доступ к группе, если доверяете не всем участникам."
                ),
            )
        )
    if (
        interactive
        and outbound_allow_to is None
        and _channel_tool_profile_may_send_outbound(tool_profile)
    ):
        outbound_default_values = normalized_outbound_allow_to or _default_outbound_allow_to(
            allow_from=normalized_allow_from,
            groups=normalized_groups,
        )
        restrict_outbound = resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Restrict channel.send outbound targets?",
            prompt_ru="Ограничить, куда channel.send может отправлять сообщения?",
            default=bool(outbound_default_values),
            lang=lang,
            detail_en=(
                "`channel.send` lets the agent initiate channel messages through this endpoint. "
                "Choose Yes to limit it to specific chat/user IDs. Choose No only for fully trusted "
                "profiles and credentials."
            ),
            detail_ru=(
                "`channel.send` позволяет агенту самому отправлять сообщения через этот канал. "
                "Выберите Да, чтобы ограничить отправку конкретными chat/user ID. Нет выбирайте только "
                "для полностью доверенных профилей и учётных данных."
            ),
        )
        if restrict_outbound:
            normalized_outbound_allow_to = split_channel_access_list(
                resolve_channel_text(
                    value=None,
                    interactive=True,
                    prompt_en="Allowed outbound chat/user ids",
                    prompt_ru="Chat/user ID для исходящих сообщений",
                    default=", ".join(outbound_default_values) if outbound_default_values else None,
                    lang=lang,
                    detail_en=(
                        "Enter platform chat/user IDs that `channel.send` may target through this endpoint."
                    ),
                    detail_ru=(
                        "Введите chat/user ID платформы, куда `channel.send` может отправлять через этот канал."
                    ),
                )
            )
        else:
            normalized_outbound_allow_to = ()
    return ChannelAccessPolicy(
        private_policy=resolved_private_policy,
        allow_from=normalized_allow_from,
        group_policy=resolved_group_policy,
        groups=normalized_groups,
        group_allow_from=normalized_group_allow_from,
        outbound_allow_to=normalized_outbound_allow_to,
    )


def split_channel_access_list(value: str | None) -> tuple[str, ...]:
    """Split comma-separated channel access ids while preserving Telegram signs."""

    if value is None:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _channel_tool_profile_may_send_outbound(tool_profile: ChannelToolProfile | None) -> bool:
    """Return whether this channel tool profile can expose channel.send."""

    if tool_profile is None:
        return False
    allowed_tools = allowed_tool_names_for_channel_profile(tool_profile)
    return allowed_tools is None or "channel.send" in set(allowed_tools)


def _default_outbound_allow_to(
    *,
    allow_from: tuple[str, ...],
    groups: tuple[str, ...],
) -> tuple[str, ...]:
    """Suggest outbound targets that match already-entered inbound allowlists."""

    normalized: list[str] = []
    seen: set[str] = set()
    for item in (*allow_from, *groups):
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def put_access_policy_bindings(
    *,
    settings: Settings,
    endpoint_id: str,
    transport: str,
    profile_id: str,
    session_policy: SessionPolicy,
    priority: int,
    enabled: bool,
    account_id: str,
    prompt_overlay: str | None,
    access_policy: ChannelAccessPolicy,
    replace_existing: bool = False,
) -> int:
    """Create scoped bindings from access policy and return how many were created."""

    if replace_existing:
        run_channel_binding_service_sync(
            settings,
            lambda service: _delete_existing_access_policy_bindings(
                service=service,
                endpoint_id=endpoint_id,
                transport=transport,
            ),
        )
    rules = build_access_policy_binding_rules(
        endpoint_id=endpoint_id,
        transport=transport,
        profile_id=profile_id,
        session_policy=session_policy,
        priority=priority,
        enabled=enabled,
        account_id=account_id,
        prompt_overlay=prompt_overlay,
        access_policy=access_policy,
    )
    for rule in rules:

        async def _put_rule(
            service: ChannelBindingService, rule: ChannelBindingRule = rule
        ) -> ChannelBindingRule:
            return await service.put(rule)

        run_channel_binding_service_sync(settings, _put_rule)
    return len(rules)


async def _delete_existing_access_policy_bindings(
    *,
    service: ChannelBindingService,
    endpoint_id: str,
    transport: str,
) -> None:
    existing = await service.list(transport=transport)
    for rule in existing:
        if rule.binding_id == endpoint_id or rule.binding_id.startswith(f"{endpoint_id}:"):
            try:
                await service.delete(binding_id=rule.binding_id)
            except ChannelBindingServiceError as exc:
                if exc.error_code != "channel_binding_not_found":
                    raise


def build_access_policy_binding_rules(
    *,
    endpoint_id: str,
    transport: str,
    profile_id: str,
    session_policy: SessionPolicy,
    priority: int,
    enabled: bool,
    account_id: str,
    prompt_overlay: str | None,
    access_policy: ChannelAccessPolicy,
) -> tuple[ChannelBindingRule, ...]:
    """Project one endpoint access policy into concrete routing rules."""

    rules: list[ChannelBindingRule] = []
    if access_policy.private_policy == "allowlist":
        for sender_id in access_policy.allow_from:
            if sender_id == "*":
                rules.append(
                    _build_access_binding_rule(
                        binding_id=f"{endpoint_id}:dm:any",
                        transport=transport,
                        profile_id=profile_id,
                        session_policy=session_policy,
                        priority=priority,
                        enabled=enabled,
                        account_id=account_id,
                        peer_id=None,
                        user_id=None,
                        prompt_overlay=prompt_overlay,
                    )
                )
            else:
                private_peer_id = None if transport == "partyflow" else sender_id
                rules.append(
                    _build_access_binding_rule(
                        binding_id=f"{endpoint_id}:dm:{sender_id}",
                        transport=transport,
                        profile_id=profile_id,
                        session_policy=session_policy,
                        priority=priority,
                        enabled=enabled,
                        account_id=account_id,
                        peer_id=private_peer_id,
                        user_id=sender_id,
                        prompt_overlay=prompt_overlay,
                    )
                )
    if access_policy.group_policy == "allowlist":
        sender_ids = access_policy.group_allow_from or access_policy.allow_from
        for group_id in access_policy.groups:
            for sender_id in sender_ids:
                rules.append(
                    _build_access_binding_rule(
                        binding_id=(
                            f"{endpoint_id}:group:{group_id}:user:{sender_id}"
                            if sender_id != "*"
                            else f"{endpoint_id}:group:{group_id}:any"
                        ),
                        transport=transport,
                        profile_id=profile_id,
                        session_policy=session_policy,
                        priority=priority,
                        enabled=enabled,
                        account_id=account_id,
                        peer_id=None if group_id == "*" else group_id,
                        user_id=None if sender_id == "*" else sender_id,
                        prompt_overlay=prompt_overlay,
                    )
                )
    if access_policy.private_policy == "open" or access_policy.group_policy == "open":
        rules.append(
            _build_access_binding_rule(
                binding_id=endpoint_id,
                transport=transport,
                profile_id=profile_id,
                session_policy=session_policy,
                priority=priority,
                enabled=enabled,
                account_id=account_id,
                peer_id=None,
                user_id=None,
                prompt_overlay=prompt_overlay,
            )
        )
    return tuple(rules)


def _build_access_binding_rule(
    *,
    binding_id: str,
    transport: str,
    profile_id: str,
    session_policy: SessionPolicy,
    priority: int,
    enabled: bool,
    account_id: str,
    peer_id: str | None,
    user_id: str | None,
    prompt_overlay: str | None,
) -> ChannelBindingRule:
    return ChannelBindingRule(
        binding_id=binding_id,
        transport=transport,
        profile_id=profile_id,
        session_policy=session_policy,
        priority=priority,
        enabled=enabled,
        account_id=account_id,
        peer_id=peer_id,
        user_id=user_id,
        prompt_overlay=prompt_overlay,
    )


def resolve_binding_update_inputs(
    *,
    settings: Settings,
    binding_id: str,
    session_policy: SessionPolicy | None,
    session_policy_default: SessionPolicy,
    priority: int | None,
    prompt_overlay: str | None,
) -> ResolvedBindingUpdateInputs:
    """Resolve binding update fields while preserving existing values when omitted."""

    existing: ChannelBindingRule | None = None
    try:
        existing = run_channel_binding_service_sync(
            settings,
            lambda service: service.get(binding_id=binding_id),
        )
    except ChannelBindingServiceError as exc:
        if exc.error_code != "channel_binding_not_found":
            raise
    return ResolvedBindingUpdateInputs(
        session_policy=(
            normalize_session_policy(session_policy)
            if session_policy is not None
            else (existing.session_policy if existing is not None else session_policy_default)
        ),
        priority=priority
        if priority is not None
        else (existing.priority if existing is not None else 0),
        prompt_overlay=prompt_overlay
        if prompt_overlay is not None
        else (existing.prompt_overlay if existing is not None else None),
    )


def build_ingress_batch_config(
    *,
    enabled: bool,
    debounce_ms: int,
    cooldown_sec: int,
    max_batch_size: int,
    max_buffer_chars: int,
) -> ChannelIngressBatchConfig:
    """Build one shared ingress-batching config from CLI flag values."""

    return ChannelIngressBatchConfig(
        enabled=enabled,
        debounce_ms=debounce_ms,
        cooldown_sec=cooldown_sec,
        max_batch_size=max_batch_size,
        max_buffer_chars=max_buffer_chars,
    )


def render_ingress_batch_summary(config: ChannelIngressBatchConfig) -> str:
    """Render one short operator-facing ingress batching summary."""

    if not config.enabled:
        return "off"
    cooldown = f",cooldown={config.cooldown_sec}s" if config.cooldown_sec > 0 else ""
    return (
        f"on(delay={config.debounce_ms}ms{cooldown},"
        f"size={config.max_batch_size},chars={config.max_buffer_chars})"
    )


def build_reply_humanization_config(
    *,
    enabled: bool,
    min_delay_ms: int,
    max_delay_ms: int,
    chars_per_second: int,
) -> ChannelReplyHumanizationConfig:
    """Build one shared reply-humanization config from CLI flag values."""

    return ChannelReplyHumanizationConfig(
        enabled=enabled,
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
        chars_per_second=chars_per_second,
    )


def merge_ingress_batch_config(
    *,
    current: ChannelIngressBatchConfig,
    enabled: bool | None = None,
    debounce_ms: int | None = None,
    cooldown_sec: int | None = None,
    max_batch_size: int | None = None,
    max_buffer_chars: int | None = None,
) -> ChannelIngressBatchConfig:
    """Merge optional CLI overrides into one ingress-batch config."""

    return ChannelIngressBatchConfig(
        enabled=current.enabled if enabled is None else enabled,
        debounce_ms=current.debounce_ms if debounce_ms is None else debounce_ms,
        cooldown_sec=current.cooldown_sec if cooldown_sec is None else cooldown_sec,
        max_batch_size=current.max_batch_size if max_batch_size is None else max_batch_size,
        max_buffer_chars=current.max_buffer_chars if max_buffer_chars is None else max_buffer_chars,
    )


def merge_reply_humanization_config(
    *,
    current: ChannelReplyHumanizationConfig,
    enabled: bool | None = None,
    min_delay_ms: int | None = None,
    max_delay_ms: int | None = None,
    chars_per_second: int | None = None,
) -> ChannelReplyHumanizationConfig:
    """Merge optional CLI overrides into one reply-humanization config."""

    return ChannelReplyHumanizationConfig(
        enabled=current.enabled if enabled is None else enabled,
        min_delay_ms=current.min_delay_ms if min_delay_ms is None else min_delay_ms,
        max_delay_ms=current.max_delay_ms if max_delay_ms is None else max_delay_ms,
        chars_per_second=current.chars_per_second if chars_per_second is None else chars_per_second,
    )


def render_reply_humanization_summary(config: ChannelReplyHumanizationConfig) -> str:
    """Render one short operator-facing reply-humanization summary."""

    if not config.enabled:
        return "off"
    return (
        f"on(delay={config.min_delay_ms}-{config.max_delay_ms}ms,"
        f"speed={config.chars_per_second}cps)"
    )


def normalize_channel_choice_value(value: str) -> str:
    """Normalize one persisted CLI choice value to canonical lowercase form."""

    return value.strip().lower()


def resolve_channel_update_profile_id(*, profile_id: str | None, current_profile_id: str) -> str:
    """Resolve one channel update profile id using the current value when no override was passed."""

    return current_profile_id if profile_id is None else normalize_channel_choice_value(profile_id)


def normalize_channel_tool_profile(value: str) -> ChannelToolProfile:
    """Normalize one channel tool-profile choice to its literal type."""

    return cast(ChannelToolProfile, normalize_channel_choice_value(value))


def normalize_session_policy(value: str) -> SessionPolicy:
    """Normalize one binding session-policy choice to its literal type."""

    return cast(SessionPolicy, normalize_channel_choice_value(value))
