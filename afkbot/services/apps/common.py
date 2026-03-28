"""Shared helpers for unified app runtime actions."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.policy import PolicyEngine, PolicyViolationError
from afkbot.services.tools.credential_placeholders import (
    resolve_secret_placeholders,
)
from afkbot.settings import Settings

_PLACEHOLDER_SKIP_FIELD_SUFFIXES = ("_credential_name",)


@dataclass(frozen=True, slots=True)
class AppCallContext:
    """Normalized context for one app action execution."""

    profile_id: str
    app_name: str
    action: str
    profile_name: str | None


def _contains_secret_placeholders(value: str) -> bool:
    """Return True when string contains canonical credential placeholder syntax."""

    lowered = value.lower()
    return "${{cred:" in lowered or "${cred_" in lowered


def _contains_legacy_credential_placeholder(value: str) -> bool:
    """Return True when old unsupported `{{credential:...}}` syntax is present."""

    return "{{credential:" in value.lower()


async def _resolve_inline_secret_placeholders(
    *,
    settings: Settings,
    context: AppCallContext,
    source: str,
) -> str:
    """Resolve all credential placeholders in one string using shared helper."""

    return await resolve_secret_placeholders(
        settings=settings,
        profile_id=context.profile_id,
        source=source,
        default_app_name=context.app_name,
        default_profile_name=context.profile_name,
        tool_name="app.run",
        allowed_app_names={context.app_name},
    )


async def resolve_credential_placeholders(
    *,
    settings: Settings,
    context: AppCallContext,
    params: dict[str, object],
) -> dict[str, object]:
    """Resolve `${{CRED:...}}` placeholders in nested app params payload."""

    cache: dict[str, str] = {}

    async def _resolve(value: object, *, field_name: str | None = None) -> object:
        if field_name is not None:
            lowered = field_name.strip().lower()
            if lowered.endswith(_PLACEHOLDER_SKIP_FIELD_SUFFIXES):
                return value

        if isinstance(value, dict):
            return {
                str(key): await _resolve(item, field_name=str(key))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [await _resolve(item) for item in value]
        if isinstance(value, tuple):
            return [await _resolve(item) for item in value]
        if not isinstance(value, str):
            return value
        if _contains_legacy_credential_placeholder(value):
            raise CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason=(
                    "Unsupported credential placeholder syntax. "
                    "Use `${{CRED:APP/PROFILE/SLUG}}`, `${{CRED:APP/SLUG}}`, or `${{CRED:SLUG}}`."
                ),
                details={
                    "integration_name": context.app_name,
                    "tool_name": "app.run",
                    "credential_profile_key": context.profile_name,
                },
            )

        cached = cache.get(value)
        if cached is not None:
            return cached
        resolved = await _resolve_inline_secret_placeholders(
            settings=settings,
            context=context,
            source=value,
        )
        cache[value] = resolved
        return resolved

    resolved = await _resolve(params)
    if not isinstance(resolved, dict):
        return {}
    return {str(key): value for key, value in resolved.items()}


async def resolve_credential_value(
    *,
    settings: Settings,
    context: AppCallContext,
    credential_slug: str,
) -> str:
    """Resolve one credential plaintext for current app action context."""

    normalized_slug = credential_slug.strip()
    if _contains_legacy_credential_placeholder(normalized_slug):
        raise CredentialsServiceError(
            error_code="credentials_invalid_name",
            reason=(
                "Unsupported credential placeholder syntax. "
                "Use `${{CRED:APP/PROFILE/SLUG}}`, `${{CRED:APP/SLUG}}`, or `${{CRED:SLUG}}`."
            ),
            details={
                "integration_name": context.app_name,
                "tool_name": "app.run",
                "credential_profile_key": context.profile_name,
                "credential_name": normalized_slug,
            },
        )
    if _contains_secret_placeholders(normalized_slug):
        return await _resolve_inline_secret_placeholders(
            settings=settings,
            context=context,
            source=normalized_slug,
        )

    service = get_credentials_service(settings)
    return await service.resolve_plaintext_for_app_tool(
        profile_id=context.profile_id,
        tool_name="app.run",
        integration_name=context.app_name,
        credential_profile_key=context.profile_name,
        credential_name=normalized_slug,
    )


async def resolve_optional_bool_credential(
    *,
    settings: Settings,
    context: AppCallContext,
    credential_slug: str,
    default: bool,
) -> bool:
    """Resolve optional boolean credential value with deterministic fallback."""

    try:
        raw = await resolve_credential_value(
            settings=settings,
            context=context,
            credential_slug=credential_slug,
        )
    except CredentialsServiceError as exc:
        if exc.error_code == "credentials_missing":
            return default
        raise
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


async def ensure_host_allowed(
    *,
    settings: Settings,
    context: AppCallContext,
    host: str,
) -> None:
    """Validate outbound network host against profile policy."""

    normalized_host = host.strip()
    if not normalized_host:
        raise ValueError("Network host is empty")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            policy = await ProfilePolicyRepository(session).get_or_create_default(context.profile_id)
            PolicyEngine(root_dir=settings.root_dir).ensure_tool_call_allowed(
                policy=policy,
                tool_name="app.run",
                params={
                    "host": normalized_host,
                    "app_name": context.app_name,
                    "action": context.action,
                },
            )
    finally:
        await engine.dispose()


def credentials_error_result(
    *,
    exc: CredentialsServiceError,
    context: AppCallContext,
) -> tuple[str, str, dict[str, object]]:
    """Build deterministic error tuple for credential resolution failures."""

    return (
        exc.error_code,
        exc.reason,
        {
            "integration_name": context.app_name,
            "tool_name": "app.run",
            "credential_profile_key": context.profile_name,
            **exc.details,
        },
    )


def policy_error_result(exc: PolicyViolationError) -> tuple[str, str]:
    """Build deterministic policy violation error tuple."""

    return "profile_policy_violation", exc.reason
