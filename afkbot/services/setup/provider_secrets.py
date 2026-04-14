"""Secret and provider-credential input resolvers for setup CLI."""

from __future__ import annotations

import json
import os
import base64
import hashlib
import secrets
import shutil
import subprocess
import time
import uuid
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import typer
from cryptography.fernet import Fernet

from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg, prompt_confirm
from afkbot.services.llm.provider_catalog import (
    LLMProviderId,
    get_provider_spec,
    provider_supports_device_code_flow,
    provider_uses_oauth_token,
)
from afkbot.services.llm.token_verifier import token_expired_or_expiring_soon
from afkbot.services.llm.minimax_portal_oauth import (
    MINIMAX_PORTAL_OAUTH_CLIENT_ID,
    MINIMAX_PORTAL_REGION_CHOICES,
    MINIMAX_PORTAL_REGION_CN,
    MINIMAX_PORTAL_REGION_GLOBAL,
    MiniMaxRegion,
    extract_minimax_oauth_error_message,
    infer_minimax_portal_region_from_base_url,
    minimax_portal_oauth_base_url_for_region,
    minimax_portal_provider_base_url_for_region,
    normalize_minimax_portal_region,
    parse_minimax_portal_token_payload,
)
from afkbot.services.profile_runtime import provider_secret_field


@dataclass(frozen=True)
class ResolvedProviderApiKeyInput:
    """Effective provider token plus the runtime-secret delta to persist."""

    effective_api_key: str
    runtime_secrets_update: dict[str, str]
    preferred_base_url: str | None = None


@dataclass(frozen=True)
class _InteractiveProviderCredential:
    token: str
    runtime_secrets_update: dict[str, str]
    preferred_base_url: str | None = None


def resolve_api_key(
    *,
    provider_id: LLMProviderId,
    interactive: bool,
    defaults: dict[str, str],
    key_file: Path | None,
    lang: PromptLanguage,
    existing_key_override: str | None = None,
) -> str:
    """Resolve provider credential from env, file, persisted store, or prompt."""

    spec = get_provider_spec(provider_id)
    credential_label = _provider_credential_label(provider_id=provider_id, lang=lang)
    existing_key = (existing_key_override or "").strip() or peek_existing_api_key(
        provider_id=provider_id,
        defaults=defaults,
    )
    key_from_file = read_secret_file(key_file, lang=lang) if key_file is not None else ""

    if interactive:
        if key_from_file:
            key = key_from_file
        else:
            if existing_key:
                typer.echo(
                    msg(
                        lang,
                        en=(
                            f"A {spec.label} {credential_label} is currently configured. "
                            "Press Enter to keep it, or provide a new value."
                        ),
                        ru=(
                            f"{credential_label} для {spec.label} уже настроен. "
                            "Нажмите Enter, чтобы оставить значение, или задайте новое."
                        ),
                    )
                )
            key = _resolve_interactive_provider_credential(
                provider_id=provider_id,
                existing_key=existing_key,
                lang=lang,
            )
            if not key and existing_key:
                key = existing_key
    else:
        key = (key_from_file or existing_key).strip()
    if not key:
        raise typer.BadParameter(
            msg(
                lang,
                en=(
                    f"{spec.label} {credential_label} is required for provider={provider_id.value}. "
                    "Use provider-specific env vars, AFKBOT_LLM_API_KEY, or --llm-api-key-file in --yes mode."
                ),
                ru=(
                    f"{credential_label} для {spec.label} обязателен при provider={provider_id.value}. "
                    "В режиме --yes используйте env-переменные провайдера, AFKBOT_LLM_API_KEY или --llm-api-key-file."
                ),
            )
        )
    return key


def resolve_profile_provider_api_key(
    *,
    provider_id: LLMProviderId,
    provider_name: str,
    interactive: bool,
    defaults: dict[str, str],
    lang: PromptLanguage,
    key_file: Path | None = None,
    current_runtime_secrets: Mapping[str, str] | None = None,
    generic_api_key: str | None = None,
    provider_api_key: str | None = None,
    minimax_region: str | None = None,
    required: bool = True,
) -> ResolvedProviderApiKeyInput:
    """Resolve provider auth for setup/profile flows and describe what should be persisted."""

    provider_field = provider_secret_field(provider_name)
    current_secrets = current_runtime_secrets or {}
    preferred_base_url: str | None = None
    existing_local_key = (
        str(current_secrets.get(provider_field, "")).strip()
        or str(current_secrets.get("llm_api_key", "")).strip()
    )
    existing_effective_key = existing_local_key or peek_existing_api_key(
        provider_id=provider_id,
        defaults=defaults,
    )
    explicit_minimax_region = (minimax_region or "").strip().lower()
    existing_minimax_region = str(current_secrets.get("minimax_portal_region", "")).strip().lower()
    normalized_minimax_region: MiniMaxRegion | None = None
    interactive_minimax_region: MiniMaxRegion | None = None
    if provider_id == LLMProviderId.MINIMAX_PORTAL:
        if explicit_minimax_region and explicit_minimax_region not in MINIMAX_PORTAL_REGION_CHOICES:
            allowed = ", ".join(MINIMAX_PORTAL_REGION_CHOICES)
            raise typer.BadParameter(
                msg(
                    lang,
                    en=f"MiniMax region must be one of: {allowed}",
                    ru=f"Регион MiniMax должен быть одним из: {allowed}",
                )
            )
        normalized_minimax_region = normalize_minimax_portal_region(
            explicit_minimax_region or existing_minimax_region,
            default=infer_minimax_portal_region_from_base_url(
                str(defaults.get("AFKBOT_MINIMAX_PORTAL_BASE_URL", "")).strip() or None
            ),
        )
        if explicit_minimax_region or existing_minimax_region:
            interactive_minimax_region = normalized_minimax_region
        if explicit_minimax_region:
            preferred_base_url = minimax_portal_provider_base_url_for_region(normalized_minimax_region)
    explicit_generic = (generic_api_key or "").strip()
    explicit_provider = (provider_api_key or "").strip()
    oauth_runtime_updates: dict[str, str] = {}
    if explicit_provider:
        effective_key = explicit_provider
    elif explicit_generic:
        effective_key = explicit_generic
    elif not required and key_file is None and not existing_effective_key:
        effective_key = ""
    elif interactive and provider_uses_oauth_token(provider_id) and key_file is None:
        interactive_credential = _resolve_interactive_provider_credential_with_metadata(
            provider_id=provider_id,
            existing_key=existing_effective_key,
            lang=lang,
            minimax_region=interactive_minimax_region,
        )
        effective_key = interactive_credential.token.strip()
        if not effective_key and existing_effective_key:
            effective_key = existing_effective_key
        oauth_runtime_updates.update(interactive_credential.runtime_secrets_update)
        preferred_base_url = interactive_credential.preferred_base_url or preferred_base_url
    else:
        effective_key = resolve_api_key(
            provider_id=provider_id,
            interactive=interactive,
            defaults=defaults,
            key_file=key_file,
            lang=lang,
            existing_key_override=existing_effective_key,
        ).strip()

    runtime_secrets_update: dict[str, str] = {}
    is_oauth_provider = provider_uses_oauth_token(provider_id)
    if explicit_generic:
        runtime_secrets_update["llm_api_key"] = explicit_generic
    if explicit_provider:
        runtime_secrets_update[provider_field] = explicit_provider
    if key_file is not None and effective_key:
        if not is_oauth_provider:
            runtime_secrets_update["llm_api_key"] = effective_key
        runtime_secrets_update[provider_field] = effective_key
    elif interactive and effective_key and effective_key != existing_effective_key:
        if not is_oauth_provider:
            runtime_secrets_update["llm_api_key"] = effective_key
        runtime_secrets_update[provider_field] = effective_key
    if oauth_runtime_updates:
        runtime_secrets_update.update(oauth_runtime_updates)
    if provider_id == LLMProviderId.MINIMAX_PORTAL:
        if "minimax_portal_region" in oauth_runtime_updates:
            runtime_secrets_update["minimax_portal_region"] = oauth_runtime_updates["minimax_portal_region"]
        elif explicit_minimax_region and normalized_minimax_region is not None:
            runtime_secrets_update["minimax_portal_region"] = normalized_minimax_region

    return ResolvedProviderApiKeyInput(
        effective_api_key=effective_key,
        runtime_secrets_update=runtime_secrets_update,
        preferred_base_url=preferred_base_url,
    )


def peek_existing_api_key(
    *,
    provider_id: LLMProviderId,
    defaults: dict[str, str],
) -> str:
    """Return the currently available provider API key without prompting."""

    spec = get_provider_spec(provider_id)
    for env_name in spec.api_key_env_names:
        candidate = (os.getenv(env_name) or "").strip()
        if candidate:
            return candidate
    for env_name in spec.api_key_env_names:
        candidate = (defaults.get(env_name, "") or "").strip()
        if candidate:
            return candidate
    global_env = (os.getenv("AFKBOT_LLM_API_KEY") or "").strip()
    if global_env:
        return global_env
    return (defaults.get("AFKBOT_LLM_API_KEY", "") or "").strip()


def _provider_credential_label(*, provider_id: LLMProviderId, lang: PromptLanguage) -> str:
    if provider_uses_oauth_token(provider_id):
        return "OAuth token" if lang == PromptLanguage.EN else "OAuth токен"
    return "API key"


def _resolve_interactive_provider_credential(
    *,
    provider_id: LLMProviderId,
    existing_key: str,
    lang: PromptLanguage,
) -> str:
    return _resolve_interactive_provider_credential_with_metadata(
        provider_id=provider_id,
        existing_key=existing_key,
        lang=lang,
    ).token


def _resolve_interactive_provider_credential_with_metadata(
    *,
    provider_id: LLMProviderId,
    existing_key: str,
    lang: PromptLanguage,
    minimax_region: MiniMaxRegion | None = None,
) -> _InteractiveProviderCredential:
    spec = get_provider_spec(provider_id)
    provider_prompt_title = msg(
        lang,
        en="Setup: Provider credentials",
        ru="Настройка: Учетные данные провайдера",
    )
    if not provider_uses_oauth_token(provider_id):
        return _InteractiveProviderCredential(
            token=_prompt_hidden_credential_input(provider_id=provider_id, lang=lang),
            runtime_secrets_update={},
        )

    if existing_key and prompt_confirm(
        question=msg(
            lang,
            en=f"Keep the existing {spec.label} credential?",
            ru=f"Оставить текущее значение для {spec.label}?",
        ),
        title=provider_prompt_title,
        default=True,
        lang=lang,
    ):
        return _InteractiveProviderCredential(token=existing_key, runtime_secrets_update={})

    if provider_id == LLMProviderId.MINIMAX_PORTAL and provider_supports_device_code_flow(provider_id):
        resolved_region = minimax_region
        if resolved_region is None:
            resolved_region = _prompt_minimax_region(lang=lang)
        region_label = "CN" if resolved_region == MINIMAX_PORTAL_REGION_CN else "Global"
        if prompt_confirm(
            question=msg(
                lang,
                en=f"Start MiniMax OAuth device-code login now? (region: {region_label})",
                ru=f"Запустить сейчас MiniMax OAuth device-code логин? (регион: {region_label})",
            ),
            title=provider_prompt_title,
            default=True,
            lang=lang,
        ):
            try:
                oauth = _run_minimax_portal_device_code_flow(lang=lang, region=resolved_region)
                runtime_updates = {
                    "minimax_portal_refresh_token": oauth.refresh_token,
                    "minimax_portal_token_expires_at": str(oauth.expires_at_epoch_sec),
                    "minimax_portal_region": oauth.region,
                }
                if oauth.resource_url:
                    runtime_updates["minimax_portal_resource_url"] = oauth.resource_url
                return _InteractiveProviderCredential(
                    token=oauth.access_token,
                    runtime_secrets_update=runtime_updates,
                    preferred_base_url=minimax_portal_provider_base_url_for_region(oauth.region),
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                raise typer.BadParameter(f"MiniMax OAuth login failed: {exc}") from exc
    if provider_id == LLMProviderId.GITHUB_COPILOT and provider_supports_device_code_flow(provider_id):
        if prompt_confirm(
            question=msg(
                lang,
                en="Start GitHub Copilot device-code login now?",
                ru="Запустить сейчас GitHub Copilot device-code логин?",
            ),
            title=provider_prompt_title,
            default=True,
            lang=lang,
        ):
            try:
                return _InteractiveProviderCredential(
                    token=_run_github_copilot_device_code_flow(lang=lang),
                    runtime_secrets_update={},
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                raise typer.BadParameter(f"GitHub Copilot device login failed: {exc}") from exc

    if provider_id == LLMProviderId.OPENAI_CODEX:
        local_codex_token = _load_local_codex_access_token()
        typer.echo(
            msg(
                lang,
                en=(
                    "OpenAI Codex uses ChatGPT OAuth tokens. You can use a locally detected token, "
                    "run `codex login` in browser, or paste an access token manually."
                ),
                ru=(
                    "OpenAI Codex использует ChatGPT OAuth токены. Можно использовать локально найденный токен, "
                    "запустить `codex login` в браузере или вручную вставить access token."
                ),
            )
        )
        if local_codex_token and prompt_confirm(
            question=msg(
                lang,
                en="Use locally detected Codex token from ~/.codex/auth.json?",
                ru="Использовать локально найденный токен Codex из ~/.codex/auth.json?",
            ),
            title=provider_prompt_title,
            default=True,
            lang=lang,
        ):
            return _InteractiveProviderCredential(
                token=local_codex_token,
                runtime_secrets_update={},
            )
        if prompt_confirm(
            question=msg(
                lang,
                en="Run `codex login` now to authorize in browser?",
                ru="Запустить сейчас `codex login` для авторизации в браузере?",
            ),
            title=provider_prompt_title,
            default=True,
            lang=lang,
        ):
            refreshed_codex_token = _run_codex_login_and_load_token(lang=lang)
            if refreshed_codex_token:
                return _InteractiveProviderCredential(
                    token=refreshed_codex_token,
                    runtime_secrets_update={},
                )
    return _InteractiveProviderCredential(
        token=_prompt_hidden_credential_input(provider_id=provider_id, lang=lang),
        runtime_secrets_update={},
    )


def _prompt_hidden_credential_input(*, provider_id: LLMProviderId, lang: PromptLanguage) -> str:
    spec = get_provider_spec(provider_id)
    credential_label = _provider_credential_label(provider_id=provider_id, lang=lang)
    raw_value = typer.prompt(
        msg(
            lang,
            en=f"{spec.label} {credential_label} (hidden input)",
            ru=f"{credential_label} для {spec.label} (ввод скрыт)",
        ),
        hide_input=True,
        default="",
        show_default=False,
    )
    return str(raw_value).strip()


def _prompt_minimax_region(*, lang: PromptLanguage) -> MiniMaxRegion:
    allowed = "/".join(MINIMAX_PORTAL_REGION_CHOICES)
    default = MINIMAX_PORTAL_REGION_GLOBAL
    prompt_text = msg(
        lang,
        en=f"MiniMax OAuth region ({allowed})",
        ru=f"Регион MiniMax OAuth ({allowed})",
    )
    while True:
        value = str(typer.prompt(prompt_text, default=default)).strip().lower()
        if value in MINIMAX_PORTAL_REGION_CHOICES:
            return normalize_minimax_portal_region(value, default=default)
        typer.echo(
            msg(
                lang,
                en=f"MiniMax region must be one of: {allowed}",
                ru=f"Регион MiniMax должен быть одним из: {allowed}",
            )
        )


def _load_local_codex_access_token() -> str:
    candidates: list[Path] = []
    codex_home = (os.getenv("CODEX_HOME") or "").strip()
    if codex_home:
        candidates.append(Path(codex_home) / "auth.json")
    candidates.append(Path.home() / ".codex" / "auth.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        tokens = payload.get("tokens")
        if isinstance(tokens, dict):
            access = str(tokens.get("access_token") or "").strip()
            if access and not token_expired_or_expiring_soon(token=access):
                return access
        access = str(payload.get("access_token") or payload.get("OPENAI_API_KEY") or "").strip()
        if access and not token_expired_or_expiring_soon(token=access):
            return access
    return ""


def _run_codex_login_and_load_token(*, lang: PromptLanguage) -> str:
    """Run `codex login` and return refreshed local access token when available."""

    codex_bin = shutil.which("codex")
    if not codex_bin:
        typer.echo(
            msg(
                lang,
                en=(
                    "`codex` CLI was not found in PATH, so browser login cannot start. "
                    "Install Codex CLI or paste the OAuth token manually."
                ),
                ru=(
                    "CLI `codex` не найден в PATH, поэтому browser-login запустить нельзя. "
                    "Установите Codex CLI или вставьте OAuth токен вручную."
                ),
            )
        )
        return ""

    typer.echo(
        msg(
            lang,
            en="Starting `codex login` (browser sign-in)...",
            ru="Запускаю `codex login` (вход через браузер)...",
        )
    )
    try:
        completed = subprocess.run([codex_bin, "login"], check=False)
    except OSError as exc:
        typer.echo(
            msg(
                lang,
                en=f"Failed to start `codex login`: {exc}",
                ru=f"Не удалось запустить `codex login`: {exc}",
            )
        )
        return ""
    if completed.returncode != 0:
        typer.echo(
            msg(
                lang,
                en=(
                    f"`codex login` exited with code {completed.returncode}. "
                    "You can retry login or paste an OAuth token manually."
                ),
                ru=(
                    f"`codex login` завершился с кодом {completed.returncode}. "
                    "Можно повторить вход или вставить OAuth токен вручную."
                ),
            )
        )
        return ""

    refreshed = _load_local_codex_access_token()
    if refreshed:
        typer.echo(
            msg(
                lang,
                en="Detected a fresh token in ~/.codex/auth.json.",
                ru="Обнаружен обновлённый токен в ~/.codex/auth.json.",
            )
        )
        return refreshed

    typer.echo(
        msg(
            lang,
            en=(
                "Login completed, but no access token was found in ~/.codex/auth.json. "
                "Paste the OAuth token manually."
            ),
            ru=(
                "Логин завершён, но access token не найден в ~/.codex/auth.json. "
                "Вставьте OAuth токен вручную."
            ),
        )
    )
    return ""


@dataclass(frozen=True)
class _MiniMaxPortalDeviceAuthResult:
    access_token: str
    refresh_token: str
    expires_at_epoch_sec: int
    region: MiniMaxRegion
    resource_url: str | None = None


def _run_github_copilot_device_code_flow(*, lang: PromptLanguage) -> str:
    client_id = "Iv1.b507a08c87ecfe98"
    device_code_url = "https://github.com/login/device/code"
    access_token_url = "https://github.com/login/oauth/access_token"

    response = httpx.post(
        device_code_url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "client_id": client_id,
            "scope": "read:user",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise typer.BadParameter("GitHub device-code response is invalid.")

    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    verification_uri = str(payload.get("verification_uri") or "").strip()
    expires_in = int(payload.get("expires_in") or 0)
    interval = max(1, int(payload.get("interval") or 5))
    if not device_code or not user_code or not verification_uri or expires_in <= 0:
        raise typer.BadParameter("GitHub device-code response is incomplete.")

    typer.echo(
        msg(
            lang,
            en=f"Open {verification_uri} and enter code: {user_code}",
            ru=f"Откройте {verification_uri} и введите код: {user_code}",
        )
    )
    try:
        _ = webbrowser.open(verification_uri, new=2)
    except Exception:
        pass

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll = httpx.post(
            access_token_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30.0,
        )
        poll.raise_for_status()
        token_payload = poll.json()
        if isinstance(token_payload, dict):
            access_token = str(token_payload.get("access_token") or "").strip()
            if access_token:
                return access_token
            error = str(token_payload.get("error") or "").strip().lower()
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 2
                continue
            if error == "access_denied":
                raise typer.BadParameter(
                    msg(
                        lang,
                        en="GitHub device login was cancelled by the user.",
                        ru="GitHub device логин был отменён пользователем.",
                    )
                )
            if error == "expired_token":
                break

            description = str(token_payload.get("error_description") or "").strip()
            if description:
                raise typer.BadParameter(description)
        raise typer.BadParameter("GitHub device-code login failed.")
    raise typer.BadParameter(
        msg(
            lang,
            en="GitHub device-code login timed out. Retry setup/profile update.",
            ru="GitHub device-code логин истёк по времени. Повторите setup/profile update.",
        )
    )


def _run_minimax_portal_device_code_flow(
    *,
    lang: PromptLanguage,
    region: MiniMaxRegion,
) -> _MiniMaxPortalDeviceAuthResult:
    client_id = MINIMAX_PORTAL_OAUTH_CLIENT_ID
    base_url = minimax_portal_oauth_base_url_for_region(region)
    code_endpoint = f"{base_url}/oauth/code"
    token_endpoint = f"{base_url}/oauth/token"
    scope = "group_id profile model.completion"
    grant_type = "urn:ietf:params:oauth:grant-type:user_code"

    code_verifier = secrets.token_urlsafe(64)
    code_verifier = (code_verifier + "A" * 43)[:96]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest()
    ).decode("utf-8").rstrip("=")
    state = secrets.token_urlsafe(16)

    response = httpx.post(
        code_endpoint,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "x-request-id": str(uuid.uuid4()),
        },
        data={
            "response_type": "code",
            "client_id": client_id,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise typer.BadParameter("MiniMax OAuth authorization payload is invalid.")

    user_code = str(payload.get("user_code") or "").strip()
    verification_uri = str(payload.get("verification_uri") or "").strip()
    payload_state = str(payload.get("state") or "").strip()
    if payload_state != state:
        raise typer.BadParameter("MiniMax OAuth state mismatch.")
    if not user_code or not verification_uri:
        raise typer.BadParameter("MiniMax OAuth authorization response is incomplete.")
    interval = float(payload.get("interval") or 2000)
    interval_sec = max(1.0, interval / 1000.0 if interval > 100 else interval)
    deadline = _resolve_minimax_deadline(payload.get("expired_in"))

    typer.echo(
        msg(
            lang,
            en=f"Open {verification_uri} and enter code: {user_code}",
            ru=f"Откройте {verification_uri} и введите код: {user_code}",
        )
    )
    try:
        _ = webbrowser.open(verification_uri, new=2)
    except Exception:
        pass

    while time.monotonic() < deadline:
        poll = httpx.post(
            token_endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": grant_type,
                "client_id": client_id,
                "user_code": user_code,
                "code_verifier": code_verifier,
            },
            timeout=30.0,
        )
        body_text = poll.text.strip()
        token_payload: dict[str, object] = {}
        if body_text:
            try:
                decoded = poll.json()
            except ValueError:
                decoded = {}
            if isinstance(decoded, dict):
                token_payload = decoded
        if poll.is_success:
            status = str(token_payload.get("status") or "").strip().lower()
            if status == "success" or (not status and "access_token" in token_payload):
                token = parse_minimax_portal_token_payload(token_payload)
                return _MiniMaxPortalDeviceAuthResult(
                    access_token=token.access_token,
                    refresh_token=token.refresh_token,
                    expires_at_epoch_sec=token.expires_at_epoch_sec,
                    region=region,
                    resource_url=token.resource_url,
                )
            if status == "error":
                message = extract_minimax_oauth_error_message(token_payload)
                if message:
                    raise typer.BadParameter(message)
                raise typer.BadParameter("MiniMax OAuth login failed.")
        else:
            message = extract_minimax_oauth_error_message(token_payload)
            if message and "not authorized" not in message.lower():
                raise typer.BadParameter(message)

        time.sleep(interval_sec)
    raise typer.BadParameter(
        msg(
            lang,
            en="MiniMax OAuth login timed out. Retry setup/profile update.",
            ru="MiniMax OAuth логин истёк по времени. Повторите setup/profile update.",
        )
    )


def _resolve_minimax_deadline(raw_expired_in: object) -> float:
    now_wall = time.time()
    now_mono = time.monotonic()
    if isinstance(raw_expired_in, int | float):
        raw = float(raw_expired_in)
    elif isinstance(raw_expired_in, str) and raw_expired_in.strip():
        raw = float(raw_expired_in.strip())
    else:
        return now_mono + 300.0

    # MiniMax endpoint may return epoch timestamp (seconds/ms) or TTL seconds.
    if raw > 100_000_000_000:  # epoch ms
        return now_mono + max(1.0, raw / 1000.0 - now_wall)
    if raw > 1_000_000_000:  # epoch sec
        return now_mono + max(1.0, raw - now_wall)
    return now_mono + max(1.0, raw)


def read_secret_file(path: Path, *, lang: PromptLanguage = PromptLanguage.EN) -> str:
    """Read one required secret value from file."""

    raw = path.read_text(encoding="utf-8")
    value = raw.strip()
    if not value:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"Credential file is empty: {path}",
                ru=f"Файл с credential пустой: {path}",
            )
        )
    return value


def resolve_credentials_master_keys(
    *,
    interactive: bool,
    existing: str,
    lang: PromptLanguage,
) -> str:
    """Resolve credentials encryption keys with deterministic auto-generation fallback."""

    key_from_env = (os.getenv("AFKBOT_CREDENTIALS_MASTER_KEYS") or "").strip()
    if key_from_env:
        return key_from_env
    normalized_existing = existing.strip()
    if normalized_existing:
        return normalized_existing

    generated = Fernet.generate_key().decode("utf-8")
    if interactive:
        typer.echo(
            msg(
                lang,
                en="Generated an encryption key for stored credentials.",
                ru="Сгенерирован ключ шифрования для сохранённых credentials.",
            )
        )
    return generated
