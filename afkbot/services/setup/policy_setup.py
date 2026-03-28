"""Setup-time policy orchestration."""

from __future__ import annotations

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.policy.presets_contracts import PolicySelection, ResolvedPolicy
from afkbot.services.policy import default_allowed_directories
from afkbot.services.policy.presets_resolver import resolve_policy
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


async def apply_setup_policy(
    *,
    settings: Settings,
    profile_id: str,
    selection: PolicySelection,
    network_allowlist: tuple[str, ...],
    resolved_policy: ResolvedPolicy | None = None,
) -> ResolvedPolicy:
    """Resolve and persist setup policy for one profile."""

    if resolved_policy is None:
        tool_registry = ToolRegistry.from_settings(settings)
        resolved = resolve_policy(
            selection=selection,
            available_tool_names=tool_registry.list_names(),
        )
    else:
        resolved = resolved_policy

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await create_schema(engine)
        async with session_scope(session_factory) as session:
            await ProfileRepository(session).get_or_create_default(profile_id)
            await ProfilePolicyRepository(session).apply_resolved_policy(
                profile_id=profile_id,
                policy_enabled=resolved.enabled,
                policy_preset=resolved.preset.value,
                policy_capabilities=tuple(item.value for item in resolved.capabilities),
                allowed_tools=resolved.allowed_tools,
                allowed_directories=default_allowed_directories(
                    root_dir=settings.root_dir,
                    profile_root=settings.profiles_dir / profile_id,
                    profile_id=profile_id,
                ),
                max_iterations_main=resolved.max_iterations_main,
                max_iterations_subagent=resolved.max_iterations_subagent,
                network_allowlist=network_allowlist,
            )
    finally:
        await engine.dispose()

    return resolved
