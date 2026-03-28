"""Credential placeholder resolution and secret redaction helpers."""

from __future__ import annotations

from collections.abc import Iterable
import re

from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.credentials.env_alias import compute_env_key
from afkbot.services.credentials.recovery import (
    build_missing_credential_metadata,
    build_missing_credential_reason,
)
from afkbot.settings import Settings

_CRED_PLACEHOLDER_RE = re.compile(r"\$\{\{CRED:([^{}]+)\}\}")
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


async def resolve_secret_placeholders(
    *,
    settings: Settings,
    profile_id: str,
    source: str,
    default_app_name: str | None,
    default_profile_name: str | None,
    tool_name: str,
    allowed_app_names: set[str] | None = None,
    resolved_values: set[str] | None = None,
) -> str:
    """Resolve `${{CRED:...}}` and `${CRED_*}` placeholders into plaintext values."""

    if not source:
        return source
    if "${{CRED:" not in source and "${CRED_" not in source:
        return source

    service = get_credentials_service(settings)
    resolved = source
    cache: dict[tuple[str, str, str], str] = {}
    env_key_map: dict[str, list[tuple[str, str, str]]] | None = None
    normalized_default_profile = (
        None if default_profile_name is None or not default_profile_name.strip() else default_profile_name.strip()
    )
    normalized_allowed_apps = _normalize_allowed_app_names(allowed_app_names)

    for match in _CRED_PLACEHOLDER_RE.finditer(source):
        placeholder = match.group(0)
        selector = match.group(1).strip()
        app_name, profile_name, credential_slug = _parse_cred_selector(
            selector=selector,
            default_app_name=default_app_name,
            default_profile_name=normalized_default_profile,
        )
        _ensure_allowed_app_name(
            app_name=app_name,
            allowed_app_names=normalized_allowed_apps,
            tool_name=tool_name,
        )
        value = await _resolve_plaintext_credential(
            service=service,
            profile_id=profile_id,
            app_name=app_name,
            profile_name=profile_name,
            credential_slug=credential_slug,
            tool_name=tool_name,
            cache=cache,
        )
        if resolved_values is not None:
            resolved_values.add(value)
        resolved = resolved.replace(placeholder, value)

    for match in _ENV_PLACEHOLDER_RE.finditer(source):
        env_key = match.group(1).strip()
        if not env_key.startswith("CRED_"):
            continue
        if env_key_map is None:
            env_key_map = await _build_env_key_map(
                service=service,
                profile_id=profile_id,
                allowed_app_names=normalized_allowed_apps,
            )
        candidates = env_key_map.get(env_key)
        if not candidates:
            raise _missing_credentials_error(
                app_name=default_app_name or "global",
                profile_name=normalized_default_profile or "default",
                credential_slug=env_key,
                tool_name=tool_name,
                reason=f"Credential alias not found: {env_key}",
                extra_details={"env_key": env_key},
            )
        if len(candidates) > 1:
            raise CredentialsServiceError(
                error_code="credential_binding_conflict",
                reason=f"Credential alias is ambiguous: {env_key}",
                details={
                    "env_key": env_key,
                    "tool_name": tool_name,
                },
            )
        app_name, profile_name, credential_slug = candidates[0]
        value = await _resolve_plaintext_credential(
            service=service,
            profile_id=profile_id,
            app_name=app_name,
            profile_name=profile_name,
            credential_slug=credential_slug,
            tool_name=tool_name,
            cache=cache,
        )
        if resolved_values is not None:
            resolved_values.add(value)
        resolved = resolved.replace(match.group(0), value)
    return resolved


def redact_secret_fragments(*, source: str, secret_values: Iterable[str]) -> str:
    """Redact known secret plaintext fragments from one string."""

    redacted = source
    ordered_values = sorted(
        {value for value in secret_values if isinstance(value, str) and value},
        key=len,
        reverse=True,
    )
    for value in ordered_values:
        redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def redact_secret_values_in_payload(*, value: object, secret_values: Iterable[str]) -> object:
    """Redact known secret plaintext fragments in nested payload object."""

    if isinstance(value, str):
        return redact_secret_fragments(source=value, secret_values=secret_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {
            str(key): redact_secret_values_in_payload(value=item, secret_values=secret_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_values_in_payload(value=item, secret_values=secret_values) for item in value]
    if isinstance(value, tuple):
        return [redact_secret_values_in_payload(value=item, secret_values=secret_values) for item in value]
    if isinstance(value, set):
        return [redact_secret_values_in_payload(value=item, secret_values=secret_values) for item in value]
    return value


async def _resolve_plaintext_credential(
    *,
    service: object,
    profile_id: str,
    app_name: str,
    profile_name: str | None,
    credential_slug: str,
    tool_name: str,
    cache: dict[tuple[str, str, str], str],
) -> str:
    normalized_app = app_name.strip() or "global"
    normalized_profile = None if profile_name is None or not profile_name.strip() else profile_name.strip()
    normalized_slug = credential_slug.strip()
    cache_key = (normalized_app, normalized_profile or "<auto>", normalized_slug)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        raw_value = await service.resolve_plaintext_for_app_tool(  # type: ignore[attr-defined]
            profile_id=profile_id,
            tool_name="app.run",
            integration_name=normalized_app,
            credential_profile_key=normalized_profile,
            credential_name=normalized_slug,
        )
        value = str(raw_value)
    except CredentialsServiceError as exc:
        if exc.error_code == "credentials_not_found":
            raise _missing_credentials_error(
                app_name=normalized_app,
                profile_name=normalized_profile or "default",
                credential_slug=normalized_slug,
                tool_name=tool_name,
            ) from exc
        if exc.error_code == "credentials_missing":
            details = {
                "integration_name": normalized_app,
                "credential_name": normalized_slug,
                "tool_name": tool_name,
                **exc.details,
            }
            if normalized_profile is not None:
                details.setdefault("credential_profile_key", normalized_profile)
            raise CredentialsServiceError(
                error_code="credentials_missing",
                reason=exc.reason,
                details=details,
            ) from exc
        raise
    cache[cache_key] = value
    return value


def _parse_cred_selector(
    *,
    selector: str,
    default_app_name: str | None,
    default_profile_name: str | None,
) -> tuple[str, str | None, str]:
    raw_parts = selector.split("/")
    if any(not part.strip() for part in raw_parts):
        raise CredentialsServiceError(
            error_code="credentials_invalid_name",
            reason=f"Invalid credential selector: {selector}",
        )
    parts = [part.strip() for part in raw_parts]
    if len(parts) == 3:
        app_name, profile_name, credential_slug = parts
        return app_name, profile_name, credential_slug
    if len(parts) == 2:
        app_name, credential_slug = parts
        return app_name, default_profile_name, credential_slug
    if len(parts) == 1:
        credential_slug = parts[0]
        app_name = (default_app_name or "global").strip() or "global"
        return app_name, default_profile_name, credential_slug
    raise CredentialsServiceError(
        error_code="credentials_invalid_name",
        reason=f"Invalid credential selector: {selector}",
    )


async def _build_env_key_map(
    *,
    service: object,
    profile_id: str,
    allowed_app_names: set[str] | None,
) -> dict[str, list[tuple[str, str, str]]]:
    rows = await service.list(  # type: ignore[attr-defined]
        profile_id=profile_id,
        tool_name=None,
        integration_name=None,
        credential_profile_key=None,
        include_inactive=False,
    )
    result: dict[str, list[tuple[str, str, str]]] = {}
    for row in rows:
        tool_name = str(getattr(row, "tool_name", "") or "").strip()
        if tool_name != "app.run":
            continue
        app_name = str(getattr(row, "integration_name", "") or "").strip()
        if allowed_app_names is not None and app_name.lower() not in allowed_app_names:
            continue
        profile_name = str(getattr(row, "credential_profile_key", "") or "").strip()
        credential_slug = str(getattr(row, "credential_name", "") or "").strip()
        env_key = compute_env_key(
            app_name=app_name,
            profile_name=profile_name,
            credential_slug=credential_slug,
        )
        result.setdefault(env_key, []).append((app_name, profile_name, credential_slug))
    return result


def _normalize_allowed_app_names(allowed_app_names: set[str] | None) -> set[str] | None:
    if allowed_app_names is None:
        return None
    normalized = {item.strip().lower() for item in allowed_app_names if item.strip()}
    return normalized if normalized else set()


def _ensure_allowed_app_name(
    *,
    app_name: str,
    allowed_app_names: set[str] | None,
    tool_name: str,
) -> None:
    if allowed_app_names is None:
        return
    normalized = app_name.strip().lower()
    if normalized in allowed_app_names:
        return
    raise CredentialsServiceError(
        error_code="credentials_scope_violation",
        reason=f"Credential selector app is not allowed for {tool_name}: {app_name}",
        details={
            "tool_name": tool_name,
            "integration_name": app_name,
            "allowed_apps": sorted(allowed_app_names),
        },
    )


def _missing_credentials_error(
    *,
    app_name: str,
    profile_name: str,
    credential_slug: str,
    tool_name: str,
    reason: str | None = None,
    extra_details: dict[str, object] | None = None,
) -> CredentialsServiceError:
    return CredentialsServiceError(
        error_code="credentials_missing",
        reason=reason
        or build_missing_credential_reason(
            integration_name=app_name,
            credential_name=credential_slug,
            credential_profile_key=profile_name,
        ),
        details=build_missing_credential_metadata(
            integration_name=app_name,
            credential_name=credential_slug,
            credential_profile_key=profile_name,
            tool_name=tool_name,
            extra_details=extra_details,
        ),
    )
