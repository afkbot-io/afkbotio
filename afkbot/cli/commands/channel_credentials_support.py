"""Inline channel-credential prompts and persistence helpers."""

from __future__ import annotations

import asyncio
from typing import Literal, cast

import typer

from afkbot.cli.commands.channel_prompt_support import resolve_channel_secret
from afkbot.cli.presentation.inline_select import run_inline_single_select
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, single_hint
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.channels.telethon_user.contracts import (
    TELETHON_CREDENTIAL_API_HASH,
    TELETHON_CREDENTIAL_API_ID,
    TELETHON_CREDENTIAL_PHONE,
    TELETHON_CREDENTIAL_SESSION_STRING,
    upsert_telethon_secret,
)
from afkbot.settings import Settings

_APP_TOOL_NAME = "app.run"
_PARTYFLOW_APP_NAME = "partyflow"
_PARTYFLOW_BOT_TOKEN = "partyflow_bot_token"
_PARTYFLOW_WEBHOOK_SIGNING_SECRET = "partyflow_webhook_signing_secret"
_TELEGRAM_APP_NAME = "telegram"
_TELEGRAM_TOKEN = "telegram_token"
_TELEGRAM_CHAT_ID = "telegram_chat_id"

ChannelCredentialAction = Literal["keep", "update"]


def existing_channel_credential_names(
    *,
    settings: Settings,
    profile_id: str,
    app_name: str,
    credential_profile_key: str,
) -> set[str]:
    """Return configured credential slugs for one app/profile key pair."""

    rows = asyncio.run(
        get_credentials_service(settings).list_bindings_for_app_runtime(
            profile_id=profile_id,
            tool_name=_APP_TOOL_NAME,
            integration_name=app_name,
            credential_profile_key=credential_profile_key,
            include_inactive=False,
        )
    )
    return {item.credential_name for item in rows if item.is_active}


def resolve_channel_credential_action(
    *,
    interactive: bool,
    app_label_en: str,
    app_label_ru: str,
    existing_any: bool,
    lang: PromptLanguage,
) -> ChannelCredentialAction:
    """Resolve whether interactive channel flow should keep or update current credentials."""

    if not interactive or not existing_any:
        return "update"
    return cast(
        ChannelCredentialAction,
        _select_labeled_option(
            title=msg(lang, en=f"{app_label_en}: Credentials", ru=f"{app_label_ru}: Credentials"),
            text=msg(
                lang,
                en="Credentials for this channel already exist. Keep them or update them now?",
                ru="Для этого канала credentials уже существуют. Оставить их или обновить сейчас?",
            ),
            options=(
                (
                    "keep",
                    msg(lang, en="Keep current credentials", ru="Оставить текущие credentials"),
                ),
                (
                    "update",
                    msg(lang, en="Update credentials now", ru="Обновить credentials сейчас"),
                ),
            ),
            default="keep",
            lang=lang,
        ),
    )


def configure_telegram_channel_credentials(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    interactive: bool,
    lang: PromptLanguage,
) -> bool:
    """Collect and persist Telegram channel credentials inline when guided setup owns them."""

    existing = existing_channel_credential_names(
        settings=settings,
        profile_id=profile_id,
        app_name=_TELEGRAM_APP_NAME,
        credential_profile_key=credential_profile_key,
    )
    action = resolve_channel_credential_action(
        interactive=interactive,
        app_label_en="Telegram",
        app_label_ru="Telegram",
        existing_any=bool(existing),
        lang=lang,
    )
    if action == "keep":
        return False

    typer.echo(
        msg(
            lang,
            en=f"Credential profile key for this channel will be `{credential_profile_key}`.",
            ru=f"Для этого канала будет использован credential profile `{credential_profile_key}`.",
        )
    )
    typer.echo(
        msg(
            lang,
            en=(
                "If Telegram bot credentials are not configured yet, this wizard will save them now. "
                "Get the token from @BotFather (`/newbot` for a new bot or `/token` for an existing one)."
            ),
            ru=(
                "Если credentials Telegram-бота ещё не настроены, этот мастер сохранит их сейчас. "
                "Токен можно получить у @BotFather (`/newbot` для нового бота или `/token` для существующего)."
            ),
        )
    )
    typer.echo(
        msg(
            lang,
            en="Optional default chat id examples: private chat `123456789`, groups/channels often start with `-100...`.",
            ru="Примеры необязательного chat id по умолчанию: личный чат `123456789`, у групп и каналов id часто начинается с `-100...`.",
        )
    )
    token = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="Telegram bot token",
        prompt_ru="Telegram bot token",
        lang=lang,
        existing_configured=_TELEGRAM_TOKEN in existing,
        required=True,
        detail_en="Paste the BotFather token that this channel should use to poll and send messages.",
        detail_ru="Вставьте BotFather token, который этот канал должен использовать для polling и отправки сообщений.",
    )
    default_chat_id = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="Default Telegram chat id",
        prompt_ru="Default Telegram chat id",
        lang=lang,
        existing_configured=_TELEGRAM_CHAT_ID in existing,
        required=False,
        detail_en=(
            "Optional default target for app-level Telegram actions such as `app.run telegram send_message`. "
            "Leave blank if you will always pass chat_id explicitly or only need inbound bot polling."
        ),
        detail_ru=(
            "Необязательная цель по умолчанию для app-level Telegram действий вроде `app.run telegram send_message`. "
            "Оставьте пустым, если всегда будете передавать chat_id явно или вам нужен только inbound polling бота."
        ),
    )
    if token is not None:
        _upsert_app_secret(
            settings=settings,
            profile_id=profile_id,
            app_name=_TELEGRAM_APP_NAME,
            credential_profile_key=credential_profile_key,
            credential_name=_TELEGRAM_TOKEN,
            secret_value=token,
        )
    if default_chat_id is not None:
        _upsert_app_secret(
            settings=settings,
            profile_id=profile_id,
            app_name=_TELEGRAM_APP_NAME,
            credential_profile_key=credential_profile_key,
            credential_name=_TELEGRAM_CHAT_ID,
            secret_value=default_chat_id,
        )
    return True


def configure_partyflow_channel_credentials(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    interactive: bool,
    lang: PromptLanguage,
) -> bool:
    """Collect and persist PartyFlow bot/webhook credentials inline for guided setup."""

    existing = existing_channel_credential_names(
        settings=settings,
        profile_id=profile_id,
        app_name=_PARTYFLOW_APP_NAME,
        credential_profile_key=credential_profile_key,
    )
    action = resolve_channel_credential_action(
        interactive=interactive,
        app_label_en="PartyFlow",
        app_label_ru="PartyFlow",
        existing_any=bool(existing),
        lang=lang,
    )
    if action == "keep":
        return False

    typer.echo(
        msg(
            lang,
            en=f"Credential profile key for this channel will be `{credential_profile_key}`.",
            ru=f"Для этого канала будет использован credential profile `{credential_profile_key}`.",
        )
    )
    typer.echo(
        msg(
            lang,
            en=(
                "You will need the PartyFlow bot bearer token from Integrations -> Bots and "
                "the outgoing webhook signing secret shown once when the webhook subscription is created."
            ),
            ru=(
                "Понадобятся bearer token бота из Integrations -> Bots и signing secret outgoing webhook, "
                "который показывается один раз при создании подписки."
            ),
        )
    )
    token = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="PartyFlow bot token",
        prompt_ru="PartyFlow bot token",
        lang=lang,
        existing_configured=_PARTYFLOW_BOT_TOKEN in existing,
        required=True,
        detail_en="Paste the PartyFlow bot token in the form `fri_bot_...`.",
        detail_ru="Вставьте PartyFlow bot token в формате `fri_bot_...`.",
    )
    signing_secret = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="PartyFlow webhook signing secret",
        prompt_ru="PartyFlow webhook signing secret",
        lang=lang,
        existing_configured=_PARTYFLOW_WEBHOOK_SIGNING_SECRET in existing,
        required=True,
        detail_en="Paste the signing secret generated by PartyFlow for this outgoing webhook subscription.",
        detail_ru="Вставьте signing secret, который PartyFlow сгенерировал для этой outgoing webhook subscription.",
    )
    if token is not None:
        _upsert_app_secret(
            settings=settings,
            profile_id=profile_id,
            app_name=_PARTYFLOW_APP_NAME,
            credential_profile_key=credential_profile_key,
            credential_name=_PARTYFLOW_BOT_TOKEN,
            secret_value=token,
        )
    if signing_secret is not None:
        _upsert_app_secret(
            settings=settings,
            profile_id=profile_id,
            app_name=_PARTYFLOW_APP_NAME,
            credential_profile_key=credential_profile_key,
            credential_name=_PARTYFLOW_WEBHOOK_SIGNING_SECRET,
            secret_value=signing_secret,
        )
    return True


def configure_telethon_channel_credentials(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    interactive: bool,
    lang: PromptLanguage,
) -> bool:
    """Collect and persist Telethon channel credentials inline when guided setup owns them."""

    existing = existing_channel_credential_names(
        settings=settings,
        profile_id=profile_id,
        app_name="telethon",
        credential_profile_key=credential_profile_key,
    )
    action = resolve_channel_credential_action(
        interactive=interactive,
        app_label_en="Telethon",
        app_label_ru="Telethon",
        existing_any=bool(existing),
        lang=lang,
    )
    if action == "keep":
        return False

    typer.echo(
        msg(
            lang,
            en=f"Credential profile key for this channel will be `{credential_profile_key}`.",
            ru=f"Для этого канала будет использован credential profile `{credential_profile_key}`.",
        )
    )
    typer.echo(
        msg(
            lang,
            en=(
                "You will need Telegram API credentials for the user account. "
                "Open my.telegram.org -> API development tools and copy the API id and API hash."
            ),
            ru=(
                "Понадобятся Telegram API credentials для user-аккаунта. "
                "Откройте my.telegram.org -> API development tools и скопируйте API id и API hash."
            ),
        )
    )
    typer.echo(
        msg(
            lang,
            en=(
                "Session string is optional. If you skip it now, AFKBOT will save api_id/api_hash/phone and you can finish login later with "
                "`afk channel telethon authorize <channel_id>`."
            ),
            ru=(
                "Session string необязателен. Если пропустить его сейчас, AFKBOT сохранит api_id/api_hash/phone, а вход можно завершить позже через "
                "`afk channel telethon authorize <channel_id>`."
            ),
        )
    )
    api_id = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="Telethon API id",
        prompt_ru="Telethon API id",
        lang=lang,
        existing_configured=TELETHON_CREDENTIAL_API_ID in existing,
        required=True,
        detail_en="Paste the Telegram API id from my.telegram.org for this user account.",
        detail_ru="Вставьте Telegram API id из my.telegram.org для этого user-аккаунта.",
    )
    api_hash = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="Telethon API hash",
        prompt_ru="Telethon API hash",
        lang=lang,
        existing_configured=TELETHON_CREDENTIAL_API_HASH in existing,
        required=True,
        detail_en="Paste the Telegram API hash paired with the API id from my.telegram.org.",
        detail_ru="Вставьте Telegram API hash, связанный с API id из my.telegram.org.",
    )
    phone = resolve_channel_secret(
        value=None,
        interactive=interactive,
        prompt_en="Telegram phone",
        prompt_ru="Телефон Telegram",
        lang=lang,
        existing_configured=TELETHON_CREDENTIAL_PHONE in existing,
        required=True,
        detail_en="Enter the phone number for the Telegram account that will authorize this userbot. Example: `+79990000000`.",
        detail_ru="Введите номер телефона Telegram-аккаунта, который будет авторизован для этого userbot. Пример: `+79990000000`.",
    )
    import_session_now = (
        _select_labeled_option(
            title=msg(lang, en="Telethon: Session", ru="Telethon: Session"),
            text=msg(
                lang,
                en=(
                    "If you already have a Telethon StringSession, import it now to skip login later. "
                    "Otherwise keep the default option and authorize after saving the channel."
                ),
                ru=(
                    "Если у вас уже есть Telethon StringSession, импортируйте его сейчас и пропустите последующий логин. "
                    "Иначе оставьте вариант по умолчанию и авторизуйтесь после сохранения канала."
                ),
            ),
            options=(
                (
                    "authorize_later",
                    msg(
                        lang,
                        en="Authorize later via `afk channel telethon authorize`",
                        ru="Авторизоваться позже через `afk channel telethon authorize`",
                    ),
                ),
                (
                    "import_now",
                    msg(
                        lang,
                        en="Import existing session string now",
                        ru="Импортировать готовый session string сейчас",
                    ),
                ),
            ),
            default="authorize_later"
            if TELETHON_CREDENTIAL_SESSION_STRING not in existing
            else "import_now",
            lang=lang,
        )
        if interactive
        else ("import_now" if TELETHON_CREDENTIAL_SESSION_STRING in existing else "authorize_later")
    )
    session_string = (
        resolve_channel_secret(
            value=None,
            interactive=interactive,
            prompt_en="Telethon session string",
            prompt_ru="Telethon session string",
            lang=lang,
            existing_configured=TELETHON_CREDENTIAL_SESSION_STRING in existing,
            required=True,
            detail_en=(
                "Paste a previously exported Telethon StringSession if you want this channel to start without a later `authorize` step."
            ),
            detail_ru=(
                "Вставьте ранее экспортированный Telethon StringSession, если хотите, чтобы этот канал заработал без отдельного шага `authorize`."
            ),
        )
        if import_session_now == "import_now"
        else None
    )

    updated = False
    if api_id is not None:
        _run_telethon_secret_upsert(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            credential_name=TELETHON_CREDENTIAL_API_ID,
            secret_value=api_id,
        )
        updated = True
    if api_hash is not None:
        _run_telethon_secret_upsert(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            credential_name=TELETHON_CREDENTIAL_API_HASH,
            secret_value=api_hash,
        )
        updated = True
    if phone is not None:
        _run_telethon_secret_upsert(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            credential_name=TELETHON_CREDENTIAL_PHONE,
            secret_value=phone,
        )
        updated = True
    if session_string is not None:
        _run_telethon_secret_upsert(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            credential_name=TELETHON_CREDENTIAL_SESSION_STRING,
            secret_value=session_string,
        )
        updated = True
    return updated


def _upsert_app_secret(
    *,
    settings: Settings,
    profile_id: str,
    app_name: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str,
) -> None:
    """Create or update one app credential binding for channel setup."""

    normalized = secret_value.strip()
    if not normalized:
        raise typer.BadParameter(f"Secret value is required for {app_name}/{credential_name}")
    try:
        try:
            _run_app_secret_create(
                settings=settings,
                profile_id=profile_id,
                app_name=app_name,
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                secret_value=normalized,
            )
        except CredentialsServiceError as exc:
            if exc.error_code != "credentials_conflict":
                raise
            _run_app_secret_update(
                settings=settings,
                profile_id=profile_id,
                app_name=app_name,
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                secret_value=normalized,
            )
    except CredentialsServiceError as exc:
        raise typer.BadParameter(exc.reason) from exc


def _select_labeled_option(
    *,
    title: str,
    text: str,
    options: tuple[tuple[str, str], ...],
    default: str,
    lang: PromptLanguage,
) -> str:
    """Select one bounded option while preserving human-readable labels."""

    selected = run_inline_single_select(
        title=title,
        text=text,
        options=list(options),
        default_value=default,
        hint_text=single_hint(lang),
    )
    return str(selected).strip() if selected else default


def _run_telethon_secret_upsert(
    *,
    settings: Settings,
    profile_id: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str,
) -> None:
    """Run one Telethon secret upsert in its own event loop boundary."""

    asyncio.run(
        upsert_telethon_secret(
            settings=settings,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
        )
    )


def _run_app_secret_create(
    *,
    settings: Settings,
    profile_id: str,
    app_name: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str,
) -> None:
    """Create one app secret with a fresh credentials service in sync CLI flows."""

    asyncio.run(
        get_credentials_service(settings).create(
            profile_id=profile_id,
            tool_name=_APP_TOOL_NAME,
            integration_name=app_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
            replace_existing=False,
        )
    )


def _run_app_secret_update(
    *,
    settings: Settings,
    profile_id: str,
    app_name: str,
    credential_profile_key: str,
    credential_name: str,
    secret_value: str,
) -> None:
    """Update one app secret with a fresh credentials service in sync CLI flows."""

    asyncio.run(
        get_credentials_service(settings).update(
            profile_id=profile_id,
            tool_name=_APP_TOOL_NAME,
            integration_name=app_name,
            credential_profile_key=credential_profile_key,
            credential_name=credential_name,
            secret_value=secret_value,
        )
    )
