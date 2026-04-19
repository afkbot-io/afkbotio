"""Shared automation principal parsing and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.repositories.automation_repo import AutomationRepository


@dataclass(frozen=True, slots=True)
class AutomationPrincipalRef:
    """Parsed automation principal reference."""

    profile_id: str
    automation_id: int


class AutomationPrincipalValidationError(ValueError):
    """Raised when an automation principal reference is malformed."""


class AutomationPrincipalNotFoundError(LookupError):
    """Raised when an automation principal does not exist or is deleted."""


def build_automation_principal_ref(*, profile_id: str, automation_id: int) -> str:
    """Build the canonical automation principal reference string."""

    return f"automation:{profile_id}:{automation_id}"


def parse_automation_principal_ref(actor_ref: str | None) -> AutomationPrincipalRef | None:
    """Parse one automation principal reference string."""

    normalized_ref = str(actor_ref or "").strip()
    if not normalized_ref:
        return None
    prefix, separator, remainder = normalized_ref.partition(":")
    if prefix != "automation" or separator != ":":
        return None
    profile_id, separator, automation_id = remainder.partition(":")
    normalized_profile_id = profile_id.strip()
    normalized_automation_id = automation_id.strip()
    if separator != ":" or not normalized_profile_id or not normalized_automation_id.isdigit():
        return None
    return AutomationPrincipalRef(
        profile_id=normalized_profile_id,
        automation_id=int(normalized_automation_id),
    )


async def ensure_automation_principal_exists(
    session: AsyncSession,
    *,
    actor_ref: str,
) -> AutomationPrincipalRef:
    """Require that one automation principal points at a live automation row."""

    parsed = parse_automation_principal_ref(actor_ref)
    if parsed is None:
        raise AutomationPrincipalValidationError(
            "automation actor_ref must match automation:<profile_id>:<automation_id>"
        )
    automation_row = await AutomationRepository(session).get_by_id(
        profile_id=parsed.profile_id,
        automation_id=parsed.automation_id,
    )
    if automation_row is None or automation_row[0].status == "deleted":
        raise AutomationPrincipalNotFoundError("Automation principal not found")
    return parsed
