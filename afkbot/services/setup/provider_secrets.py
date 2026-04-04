"""Secret and provider-credential input resolvers for setup CLI."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import typer
from cryptography.fernet import Fernet

from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.services.llm.provider_catalog import LLMProviderId, get_provider_spec
from afkbot.services.profile_runtime import provider_secret_field


@dataclass(frozen=True)
class ResolvedProviderApiKeyInput:
    """Effective provider token plus the runtime-secret delta to persist."""

    effective_api_key: str
    runtime_secrets_update: dict[str, str]


def resolve_api_key(
    *,
    provider_id: LLMProviderId,
    interactive: bool,
    defaults: dict[str, str],
    key_file: Path | None,
    lang: PromptLanguage,
    existing_key_override: str | None = None,
) -> str:
    """Resolve provider API key from env, file, persisted store, or prompt."""

    spec = get_provider_spec(provider_id)
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
                            f"A {spec.label} API key is already configured. "
                            "Press Enter to keep it, or paste a new key."
                        ),
                        ru=(
                            f"API key для {spec.label} уже настроен. "
                            "Нажмите Enter, чтобы оставить его, или вставьте новый ключ."
                        ),
                    )
                )
            key = typer.prompt(
                msg(
                    lang,
                    en=f"{spec.label} API key (hidden input)",
                    ru=f"API key для {spec.label} (ввод скрыт)",
                ),
                hide_input=True,
                default="",
                show_default=False,
            ).strip()
            if not key and existing_key:
                key = existing_key
    else:
        key = (key_from_file or existing_key).strip()
    if not key:
        raise typer.BadParameter(
            msg(
                lang,
                en=(
                    f"{spec.label} API key is required for provider={provider_id.value}. "
                    "Use AFKBOT_LLM_API_KEY or --llm-api-key-file in --yes mode."
                ),
                ru=(
                    f"API key для {spec.label} обязателен при provider={provider_id.value}. "
                    "В режиме --yes используйте AFKBOT_LLM_API_KEY или --llm-api-key-file."
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
    required: bool = True,
) -> ResolvedProviderApiKeyInput:
    """Resolve provider auth for setup/profile flows and describe what should be persisted."""

    provider_field = provider_secret_field(provider_name)
    current_secrets = current_runtime_secrets or {}
    existing_local_key = (
        str(current_secrets.get(provider_field, "")).strip()
        or str(current_secrets.get("llm_api_key", "")).strip()
    )
    existing_effective_key = existing_local_key or peek_existing_api_key(
        provider_id=provider_id,
        defaults=defaults,
    )
    explicit_generic = (generic_api_key or "").strip()
    explicit_provider = (provider_api_key or "").strip()
    if explicit_provider:
        effective_key = explicit_provider
    elif explicit_generic:
        effective_key = explicit_generic
    elif not required and key_file is None and not existing_effective_key:
        effective_key = ""
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
    if explicit_generic:
        runtime_secrets_update["llm_api_key"] = explicit_generic
    if explicit_provider:
        runtime_secrets_update[provider_field] = explicit_provider
    if key_file is not None and effective_key:
        runtime_secrets_update["llm_api_key"] = effective_key
        runtime_secrets_update[provider_field] = effective_key
    elif interactive and effective_key and effective_key != existing_effective_key:
        runtime_secrets_update["llm_api_key"] = effective_key
        runtime_secrets_update[provider_field] = effective_key

    return ResolvedProviderApiKeyInput(
        effective_api_key=effective_key,
        runtime_secrets_update=runtime_secrets_update,
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


def read_secret_file(path: Path, *, lang: PromptLanguage = PromptLanguage.EN) -> str:
    """Read one required secret value from file."""

    raw = path.read_text(encoding="utf-8")
    value = raw.strip()
    if not value:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"API key file is empty: {path}",
                ru=f"Файл с API key пустой: {path}",
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
