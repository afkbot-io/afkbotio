"""Profile CRUD service with profile-scoped runtime config and policy setup."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableSequence
from pathlib import Path
from typing import TypeVar

from afkbot.models.profile import Profile
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.services.atomic_writes import atomic_text_write
from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.policy import (
    ResolvedPolicy,
    apply_file_access_mode,
    default_allowed_directories,
    get_profile_files_lock,
    infer_file_access_mode,
    parse_capability_ids,
    parse_preset_level,
)
from afkbot.services.policy.presets_contracts import PolicySelection
from afkbot.services.policy.presets_resolver import resolve_policy
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.profile_runtime.deletion import purge_profile_rows, remove_profile_files
from afkbot.services.profile_runtime.contracts import (
    ProfileDetails,
    ProfilePolicyView,
    ProfileRuntimeConfig,
    ProfileSummary,
)
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.profile_runtime.runtime_secrets import get_profile_runtime_secrets_service
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[tuple[str, int], "ProfileService"] = {}
TProfileValue = TypeVar("TProfileValue")
_PROFILE_BOOTSTRAP_STARTERS: dict[str, str] = {
    "AGENTS.md": "No profile-specific role instructions. Follow the global bootstrap and the current user request.",
    "IDENTITY.md": "No profile-specific identity instructions. Use the global identity defaults.",
    "TOOLS.md": "No profile-specific tool instructions. Use the global tool defaults.",
    "SECURITY.md": "No profile-specific security instructions. Use the global security defaults.",
}
_GENERIC_PROFILE_BOOTSTRAP_STARTER = (
    "No profile-specific instructions in this bootstrap file. Use the global defaults."
)


class ProfileServiceError(ValueError):
    """Structured profile service error surfaced by CLI/API callers."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class ProfileService:
    """Manage runtime profiles backed by DB rows plus profile-local config files."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime_configs = get_profile_runtime_config_service(settings)
        self._runtime_secrets = get_profile_runtime_secrets_service(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def create(
        self,
        *,
        profile_id: str,
        name: str,
        runtime_config: ProfileRuntimeConfig,
        runtime_secrets: dict[str, str] | None,
        policy_enabled: bool,
        policy_preset: str | None,
        policy_capabilities: tuple[str, ...],
        policy_file_access_mode: str = "read_write",
        policy_allowed_directories: tuple[str, ...] | None = None,
        policy_network_allowlist: tuple[str, ...],
    ) -> ProfileDetails:
        """Create one profile with persisted runtime config and initial policy."""

        normalized_name = name.strip()
        if not normalized_name:
            raise ProfileServiceError(error_code="profile_invalid_name", reason="Profile name is required")

        resolved_policy = self._resolve_policy(
            runtime_config=runtime_config,
            policy_enabled=policy_enabled,
            policy_preset=policy_preset,
            policy_capabilities=policy_capabilities,
        )
        effective_allowed_tools = apply_file_access_mode(
            allowed_tools=resolved_policy.allowed_tools,
            file_access_mode=policy_file_access_mode,
        )
        effective_allowed_directories = tuple(policy_allowed_directories or ()) or default_allowed_directories(
            root_dir=self._settings.root_dir,
            profile_root=self._runtime_configs.profile_root(profile_id),
            profile_id=profile_id,
        )

        config_written = False
        secrets_written = False
        created_bootstrap_files: list[Path] = []

        async def _op(session: AsyncSession) -> ProfileDetails:
            nonlocal config_written, secrets_written, created_bootstrap_files
            profiles = ProfileRepository(session)
            if await profiles.get(profile_id) is not None:
                raise ProfileServiceError(
                    error_code="profile_exists",
                    reason=f"Profile already exists: {profile_id}",
                )
            row = await profiles.create(
                profile_id=profile_id,
                name=normalized_name,
                is_default=profile_id == "default",
            )
            await ProfilePolicyRepository(session).apply_resolved_policy(
                profile_id=profile_id,
                policy_enabled=resolved_policy.enabled,
                policy_preset=resolved_policy.preset.value,
                policy_capabilities=tuple(item.value for item in resolved_policy.capabilities),
                allowed_tools=effective_allowed_tools,
                allowed_directories=effective_allowed_directories,
                max_iterations_main=resolved_policy.max_iterations_main,
                max_iterations_subagent=resolved_policy.max_iterations_subagent,
                network_allowlist=policy_network_allowlist,
            )
            async with self._profile_files_lock.acquire(profile_id):
                self._runtime_configs.ensure_layout(profile_id)
                config_path = self._runtime_configs.config_path(profile_id)
                if config_path.exists():
                    raise ProfileServiceError(
                        error_code="profile_runtime_config_exists",
                        reason=f"Profile runtime config already exists: {profile_id}",
                    )
                self._runtime_configs.write(profile_id, runtime_config)
                config_written = True
                if runtime_secrets:
                    self._runtime_secrets.write(profile_id, runtime_secrets)
                    secrets_written = True
                self._seed_missing_bootstrap_files(
                    profile_id=profile_id,
                    created=created_bootstrap_files,
                )
            return await self._build_details(row=row, session=session)

        try:
            return await self._with_session(_op)
        except Exception:
            if config_written or secrets_written or created_bootstrap_files:
                async with self._profile_files_lock.acquire(profile_id):
                    for path in created_bootstrap_files:
                        if path.exists():
                            path.unlink()
                    if config_written:
                        self._runtime_configs.remove(profile_id)
                    if secrets_written:
                        self._runtime_secrets.remove(profile_id)
            raise

    async def bootstrap_default(
        self,
        *,
        runtime_config: ProfileRuntimeConfig,
        runtime_secrets: dict[str, str] | None,
        policy_enabled: bool,
        policy_preset: str | None,
        policy_capabilities: tuple[str, ...],
        policy_file_access_mode: str = "read_write",
        policy_allowed_directories: tuple[str, ...] | None = None,
        policy_network_allowlist: tuple[str, ...],
    ) -> ProfileDetails:
        """Create or replace the canonical default profile from setup answers."""

        resolved_policy = self._resolve_policy(
            runtime_config=runtime_config,
            policy_enabled=policy_enabled,
            policy_preset=policy_preset,
            policy_capabilities=policy_capabilities,
        )
        effective_allowed_tools = apply_file_access_mode(
            allowed_tools=resolved_policy.allowed_tools,
            file_access_mode=policy_file_access_mode,
        )
        effective_allowed_directories = tuple(policy_allowed_directories or ()) or default_allowed_directories(
            root_dir=self._settings.root_dir,
            profile_root=self._runtime_configs.profile_root("default"),
            profile_id="default",
        )

        async def _op(session: AsyncSession) -> ProfileDetails:
            profiles = ProfileRepository(session)
            row = await profiles.get_or_create_default("default")
            row.name = "Default"
            row.is_default = True
            row.status = "active"
            await ProfilePolicyRepository(session).apply_resolved_policy(
                profile_id="default",
                policy_enabled=resolved_policy.enabled,
                policy_preset=resolved_policy.preset.value,
                policy_capabilities=tuple(item.value for item in resolved_policy.capabilities),
                allowed_tools=effective_allowed_tools,
                allowed_directories=effective_allowed_directories,
                max_iterations_main=resolved_policy.max_iterations_main,
                max_iterations_subagent=resolved_policy.max_iterations_subagent,
                network_allowlist=policy_network_allowlist,
            )
            async with self._profile_files_lock.acquire("default"):
                self._runtime_configs.ensure_layout("default")
                self._runtime_configs.write("default", runtime_config)
                if runtime_secrets:
                    self._runtime_secrets.write("default", runtime_secrets)
                else:
                    self._runtime_secrets.remove("default")
                self._seed_missing_bootstrap_files(profile_id="default")
            await session.flush()
            return await self._build_details(row=row, session=session)

        return await self._with_session(_op)

    async def update(
        self,
        *,
        profile_id: str,
        name: str,
        runtime_config: ProfileRuntimeConfig,
        policy_enabled: bool,
        policy_preset: str | None,
        policy_capabilities: tuple[str, ...],
        policy_file_access_mode: str = "read_write",
        policy_allowed_directories: tuple[str, ...] | None = None,
        policy_network_allowlist: tuple[str, ...],
    ) -> ProfileDetails:
        """Update one profile row, runtime config, and effective policy."""

        normalized_name = name.strip()
        if not normalized_name:
            raise ProfileServiceError(error_code="profile_invalid_name", reason="Profile name is required")

        resolved_policy = self._resolve_policy(
            runtime_config=runtime_config,
            policy_enabled=policy_enabled,
            policy_preset=policy_preset,
            policy_capabilities=policy_capabilities,
        )
        effective_allowed_tools = apply_file_access_mode(
            allowed_tools=resolved_policy.allowed_tools,
            file_access_mode=policy_file_access_mode,
        )
        effective_allowed_directories = tuple(policy_allowed_directories or ()) or default_allowed_directories(
            root_dir=self._settings.root_dir,
            profile_root=self._runtime_configs.profile_root(profile_id),
            profile_id=profile_id,
        )

        async def _op(session: AsyncSession) -> ProfileDetails:
            profiles = ProfileRepository(session)
            row = await profiles.get(profile_id)
            if row is None:
                raise ProfileServiceError(
                    error_code="profile_not_found",
                    reason=f"Profile not found: {profile_id}",
                )
            row.name = normalized_name
            await ProfilePolicyRepository(session).apply_resolved_policy(
                profile_id=profile_id,
                policy_enabled=resolved_policy.enabled,
                policy_preset=resolved_policy.preset.value,
                policy_capabilities=tuple(item.value for item in resolved_policy.capabilities),
                allowed_tools=effective_allowed_tools,
                allowed_directories=effective_allowed_directories,
                max_iterations_main=resolved_policy.max_iterations_main,
                max_iterations_subagent=resolved_policy.max_iterations_subagent,
                network_allowlist=policy_network_allowlist,
            )
            async with self._profile_files_lock.acquire(profile_id):
                self._runtime_configs.ensure_layout(profile_id)
                self._runtime_configs.write(profile_id, runtime_config)
            await session.flush()
            return await self._build_details(row=row, session=session)

        return await self._with_session(_op)

    async def get(self, *, profile_id: str) -> ProfileDetails:
        """Return one detailed profile view."""

        async def _op(session: AsyncSession) -> ProfileDetails:
            profiles = ProfileRepository(session)
            row = await profiles.get(profile_id)
            if row is None:
                raise ProfileServiceError(
                    error_code="profile_not_found",
                    reason=f"Profile not found: {profile_id}",
                )
            return await self._build_details(row=row, session=session)

        return await self._with_session(_op)

    async def list(self) -> list[ProfileSummary]:
        """Return runtime profile summaries."""

        async def _op(session: AsyncSession) -> list[ProfileSummary]:
            rows = await ProfileRepository(session).list_all()
            return [self._build_summary(row) for row in rows]

        return await self._with_session(_op)

    async def delete(self, *, profile_id: str) -> ProfileSummary:
        """Delete one non-default profile, its linked runtime data, and profile folder."""

        if profile_id == "default":
            raise ProfileServiceError(
                error_code="profile_delete_default_forbidden",
                reason="Default profile cannot be deleted.",
            )

        profile_root = self._runtime_configs.profile_root(profile_id)
        endpoint_service = get_channel_endpoint_service(self._settings)

        async def _op(session: AsyncSession) -> tuple[ProfileSummary, tuple[str, ...]]:
            profiles = ProfileRepository(session)
            row = await profiles.get(profile_id)
            if row is None:
                raise ProfileServiceError(
                    error_code="profile_not_found",
                    reason=f"Profile not found: {profile_id}",
                )
            summary = self._build_summary(row)
            endpoint_ids = await purge_profile_rows(session=session, profile_id=profile_id)
            return summary, endpoint_ids

        async with self._profile_files_lock.acquire(profile_id):
            summary, endpoint_ids = await self._with_session(_op)
            remove_profile_files(
                profile_root=profile_root,
                endpoint_service=endpoint_service,
                endpoint_ids=endpoint_ids,
            )
            return summary

    def _build_summary(self, row: Profile) -> ProfileSummary:
        runtime_config = self._runtime_configs.load(row.id)
        effective_settings = self._runtime_configs.build_effective_settings(
            profile_id=row.id,
            base_settings=self._settings,
        )
        return ProfileSummary(
            id=row.id,
            name=row.name,
            is_default=row.is_default,
            status=row.status,
            has_runtime_config=runtime_config is not None,
            effective_runtime=self._runtime_configs.resolved_runtime(effective_settings),
        )

    async def _build_details(self, *, row: Profile, session: AsyncSession) -> ProfileDetails:
        summary = self._build_summary(row)
        runtime_config = self._runtime_configs.load(row.id)
        policy = await self._load_policy_view(session=session, profile_id=row.id)
        profile_root = self._runtime_configs.profile_root(row.id)
        system_dir = self._runtime_configs.system_dir(row.id)
        config_path = self._runtime_configs.config_path(row.id)
        secrets_path = self._runtime_secrets.secrets_path(row.id)
        bootstrap_dir = self._runtime_configs.bootstrap_dir(row.id)
        skills_dir = self._runtime_configs.skills_dir(row.id)
        subagents_dir = self._runtime_configs.subagents_dir(row.id)
        return ProfileDetails(
            **summary.model_dump(),
            profile_root=self._to_relative(profile_root),
            system_dir=self._to_relative(system_dir),
            runtime_config=runtime_config,
            runtime_config_path=self._to_relative(config_path),
            runtime_secrets=self._runtime_secrets.describe(row.id),
            runtime_secrets_path=self._to_relative(secrets_path),
            bootstrap_dir=self._to_relative(bootstrap_dir),
            skills_dir=self._to_relative(skills_dir),
            subagents_dir=self._to_relative(subagents_dir),
            policy=policy,
        )

    async def _load_policy_view(self, *, session: AsyncSession, profile_id: str) -> ProfilePolicyView:
        row = await ProfilePolicyRepository(session).get(profile_id)
        if row is None:
            return ProfilePolicyView()
        allowed_tools = self._load_string_tuple(row.allowed_tools_json)
        return ProfilePolicyView(
            enabled=bool(row.policy_enabled),
            preset=str(row.policy_preset or "medium"),
            capabilities=self._load_string_tuple(row.policy_capabilities_json),
            file_access_mode=infer_file_access_mode(allowed_tools=allowed_tools),
            allowed_directories=self._load_string_tuple(
                row.allowed_directories_json,
                lowercase=False,
            ),
            network_allowlist=self._load_string_tuple(row.network_allowlist_json),
        )

    @staticmethod
    def _load_string_tuple(raw: str, *, lowercase: bool = True) -> tuple[str, ...]:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list):
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in decoded:
            if not isinstance(item, str):
                continue
            value = item.strip().lower() if lowercase else item.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return tuple(normalized)

    def _resolve_policy(
        self,
        *,
        runtime_config: ProfileRuntimeConfig,
        policy_enabled: bool,
        policy_preset: str | None,
        policy_capabilities: tuple[str, ...],
    ) -> ResolvedPolicy:
        effective_settings = self._runtime_configs.apply_to_settings(
            settings=self._settings,
            config=runtime_config,
        )
        from afkbot.services.tools.registry import ToolRegistry

        normalized_policy_preset = str(policy_preset or "medium")
        return resolve_policy(
            selection=PolicySelection(
                enabled=policy_enabled,
                preset=parse_preset_level(normalized_policy_preset),
                capabilities=parse_capability_ids(policy_capabilities),
            ),
            available_tool_names=ToolRegistry.from_settings(effective_settings).list_names(),
        )

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve(strict=False).relative_to(root))
        except ValueError:
            return str(path.resolve(strict=False))

    def _seed_missing_bootstrap_files(
        self,
        *,
        profile_id: str,
        created: MutableSequence[Path] | None = None,
    ) -> tuple[Path, ...]:
        """Create any missing profile bootstrap files with neutral starter content."""

        created_paths = created if created is not None else []
        bootstrap_dir = self._runtime_configs.bootstrap_dir(profile_id)
        for file_name in self._settings.bootstrap_files:
            path = bootstrap_dir / file_name
            if path.exists():
                continue
            starter = _PROFILE_BOOTSTRAP_STARTERS.get(file_name, _GENERIC_PROFILE_BOOTSTRAP_STARTER)
            atomic_text_write(path, starter + "\n", mode=0o600)
            created_paths.append(path)
        return tuple(created_paths)

    async def shutdown(self) -> None:
        """Dispose owned database engine."""

        await self._engine.dispose()

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TProfileValue]],
    ) -> TProfileValue:
        await self._ensure_schema()
        async with session_scope(self._session_factory) as session:
            return await op(session)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True


def get_profile_service(settings: Settings) -> ProfileService:
    """Return a profile service scoped to the current async loop when available."""

    key_root = str(settings.root_dir.resolve())
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync CLI flows often call this before wrapping work with ``asyncio.run(...)``.
        # Returning a fresh service avoids leaking one async engine across multiple event loops.
        return ProfileService(settings=settings)

    key = (key_root, id(loop))
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def run_profile_service_sync(
    settings: Settings,
    op: Callable[[ProfileService], Awaitable[TProfileValue]],
) -> TProfileValue:
    """Run one profile service operation in a fresh event loop and dispose the engine."""

    async def _run() -> TProfileValue:
        service = ProfileService(settings=settings)
        try:
            return await op(service)
        finally:
            await service.shutdown()

    return asyncio.run(_run())


def reset_profile_services() -> None:
    """Reset cached profile services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_profile_services_async() -> None:
    """Reset cached profile services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
