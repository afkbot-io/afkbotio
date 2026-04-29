"""Shared owner/ref normalization for Task Flow tool and CLI surfaces."""

from __future__ import annotations

from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.task_flow.ai_executors import (
    AI_PROFILE_OWNER_TYPE,
    AI_SUBAGENT_OWNER_TYPE,
    build_ai_subagent_owner_ref,
    normalize_task_owner_type,
    parse_ai_subagent_owner_ref,
)


class TaskOwnerInputError(ValueError):
    """Structured owner selector validation error with stable surface-facing codes."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def resolve_task_owner_inputs(
    *,
    field_prefix: str,
    owner_type: str | None,
    owner_ref: str | None,
    owner_profile_id: str | None,
    owner_subagent_name: str | None,
) -> tuple[str | None, str | None]:
    """Resolve raw or structured owner inputs into one normalized type/ref pair.

    Structured selectors accept either:
    - `<field>_profile_id` alone for one `ai_profile` target, or
    - `<field>_profile_id` plus `<field>_subagent_name` for one `ai_subagent` target.
    """

    normalized_type = normalize_task_owner_type(owner_type)
    normalized_ref = _normalize_optional_text(owner_ref)
    normalized_profile_id = _normalize_optional_text(owner_profile_id)
    normalized_subagent_name = _normalize_optional_text(owner_subagent_name)
    structured_present = (
        normalized_profile_id is not None or normalized_subagent_name is not None
    )
    if not structured_present:
        return normalized_type, normalized_ref
    if normalized_profile_id is None:
        raise TaskOwnerInputError(
            error_code="invalid_owner_ref",
            reason=f"{field_prefix}_profile_id is required when {field_prefix}_subagent_name is set",
        )

    if normalized_subagent_name is None:
        if normalized_ref is not None:
            raise TaskOwnerInputError(
                error_code="invalid_owner_ref",
                reason=(
                    f"{field_prefix}_ref cannot be combined with "
                    f"{field_prefix}_profile_id/{field_prefix}_subagent_name"
                ),
            )
        if normalized_type is not None and normalized_type != AI_PROFILE_OWNER_TYPE:
            raise TaskOwnerInputError(
                error_code="invalid_owner_type",
                reason=f"{field_prefix}_profile_id without {field_prefix}_subagent_name requires {field_prefix}_type=ai_profile",
            )
        try:
            normalized_profile_id = validate_profile_id(normalized_profile_id)
        except InvalidProfileIdError as exc:
            raise TaskOwnerInputError(
                error_code="invalid_owner_ref",
                reason=str(exc),
            ) from exc
        return AI_PROFILE_OWNER_TYPE, normalized_profile_id

    if normalized_type is not None and normalized_type != AI_SUBAGENT_OWNER_TYPE:
        raise TaskOwnerInputError(
            error_code="invalid_owner_type",
            reason=(
                f"{field_prefix}_profile_id/{field_prefix}_subagent_name "
                f"require {field_prefix}_type=ai_subagent"
            ),
        )

    try:
        normalized_structured_ref = build_ai_subagent_owner_ref(
            profile_id=normalized_profile_id,
            subagent_name=normalized_subagent_name,
        )
    except (InvalidProfileIdError, ValueError) as exc:
        raise TaskOwnerInputError(
            error_code="invalid_owner_ref",
            reason=str(exc),
        ) from exc

    if normalized_ref is None:
        return AI_SUBAGENT_OWNER_TYPE, normalized_structured_ref

    parsed_raw_ref = parse_ai_subagent_owner_ref(normalized_ref)
    if parsed_raw_ref is None:
        raise TaskOwnerInputError(
            error_code="invalid_owner_ref",
            reason=(
                f"{field_prefix}_ref must be one canonical "
                "`<profile_id>:<subagent_name>` value"
            ),
        )
    normalized_raw_ref = f"{parsed_raw_ref[0]}:{parsed_raw_ref[1]}"
    if normalized_raw_ref != normalized_structured_ref:
        raise TaskOwnerInputError(
            error_code="invalid_owner_ref",
            reason=(
                f"{field_prefix}_ref conflicts with "
                f"{field_prefix}_profile_id/{field_prefix}_subagent_name"
            ),
        )
    return AI_SUBAGENT_OWNER_TYPE, normalized_structured_ref


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
