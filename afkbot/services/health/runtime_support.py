"""Shared runtime helpers for health integration checks."""

from __future__ import annotations

from pathlib import Path

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.credentials import CredentialsService
from afkbot.services.health.contracts import HealthServiceError
from afkbot.services.policy import PolicyEngine
from afkbot.settings import Settings


async def ensure_profile_ready(*, settings: Settings, profile_id: str) -> None:
    """Ensure profile exists and has a policy row for integration checks."""

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            profile = await ProfileRepository(session).get(profile_id)
            if profile is None:
                raise HealthServiceError(
                    error_code="profile_not_found",
                    reason=f"Profile not found: {profile_id}",
                )
            await ProfilePolicyRepository(session).get_or_create_default(profile_id)
    finally:
        await engine.dispose()


async def available_credentials(
    *,
    service: CredentialsService,
    profile_id: str,
    integration_name: str,
    credential_profile_key: str,
) -> set[str]:
    """Return active credential names for integration/profile key."""

    bindings = await service.list(
        profile_id=profile_id,
        tool_name=None,
        include_inactive=False,
        integration_name=integration_name,
        credential_profile_key=credential_profile_key,
    )
    return {item.credential_name for item in bindings if item.is_active}


async def ensure_host_allowed(
    *,
    settings: Settings,
    profile_id: str,
    tool_name: str,
    host: str,
) -> None:
    """Run policy allowlist validation for one integration host."""

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            policy = await ProfilePolicyRepository(session).get_or_create_default(profile_id)
            PolicyEngine(root_dir=settings.root_dir).ensure_tool_call_allowed(
                policy=policy,
                tool_name=tool_name,
                params={"host": host},
            )
    finally:
        await engine.dispose()


def get_missing_bootstrap(settings: Settings) -> list[Path]:
    """Return missing mandatory bootstrap files."""

    required = [settings.bootstrap_dir / name for name in settings.bootstrap_files]
    return [path for path in required if not path.exists()]
