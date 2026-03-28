"""Profile-scoped CRUD service for bootstrap/system-prompt markdown files."""

from __future__ import annotations

from pathlib import Path

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.atomic_writes import atomic_text_write
from afkbot.services.policy import get_profile_files_lock
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime.contracts import ProfileBootstrapFileView, ProfileBootstrapRecord
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ProfileBootstrapService"] = {}


class ProfileBootstrapService:
    """Manage profile-local bootstrap files layered over global bootstrap assets."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime_configs = get_profile_runtime_config_service(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)

    async def list(self, *, profile_id: str) -> list[ProfileBootstrapFileView]:
        """List configured bootstrap slots for one profile."""

        normalized_profile_id = validate_profile_id(profile_id)
        await self._ensure_profile_exists(profile_id=normalized_profile_id)
        return [
            self._build_record(profile_id=normalized_profile_id, file_name=file_name)
            for file_name in self._settings.bootstrap_files
        ]

    async def get(
        self,
        *,
        profile_id: str,
        name: str | None = None,
        file_name: str | None = None,
    ) -> ProfileBootstrapRecord:
        """Read one existing profile bootstrap file."""

        normalized_profile_id = validate_profile_id(profile_id)
        await self._ensure_profile_exists(profile_id=normalized_profile_id)
        resolved_name = self._resolve_name(file_name or name or "")
        path = self._path(profile_id=normalized_profile_id, file_name=resolved_name)
        return ProfileBootstrapRecord(
            file_name=resolved_name,
            path=self._to_relative(path),
            content=path.read_text(encoding="utf-8") if path.exists() else None,
            exists=path.exists(),
        )

    async def upsert(self, *, profile_id: str, name: str, content: str) -> ProfileBootstrapRecord:
        """Create or replace one profile bootstrap file."""

        return await self.write(profile_id=profile_id, file_name=name, content=content)

    async def write(self, *, profile_id: str, file_name: str, content: str) -> ProfileBootstrapRecord:
        """Create or replace one profile bootstrap file."""

        normalized_profile_id = validate_profile_id(profile_id)
        await self._ensure_profile_exists(profile_id=normalized_profile_id)
        resolved_name = self._resolve_name(file_name)
        normalized_content = content.strip()
        if not normalized_content:
            raise ProfileServiceError(
                error_code="profile_bootstrap_empty",
                reason="Bootstrap content is required",
            )
        path = self._path(profile_id=normalized_profile_id, file_name=resolved_name)
        async with self._profile_files_lock.acquire(normalized_profile_id):
            self._runtime_configs.ensure_layout(normalized_profile_id)
            atomic_text_write(path, normalized_content + "\n", mode=0o600)
        return await self.get(profile_id=normalized_profile_id, file_name=resolved_name)

    async def delete(self, *, profile_id: str, name: str) -> ProfileBootstrapRecord:
        """Delete one existing profile bootstrap file."""

        return await self.remove(profile_id=profile_id, file_name=name)

    async def remove(self, *, profile_id: str, file_name: str) -> ProfileBootstrapRecord:
        """Remove one profile bootstrap file when present."""

        normalized_profile_id = validate_profile_id(profile_id)
        await self._ensure_profile_exists(profile_id=normalized_profile_id)
        resolved_name = self._resolve_name(file_name)
        path = self._path(profile_id=normalized_profile_id, file_name=resolved_name)
        async with self._profile_files_lock.acquire(normalized_profile_id):
            if path.exists():
                path.unlink()
                self._prune_empty_dirs(
                    start=path.parent,
                    stop_at=self._runtime_configs.bootstrap_dir(normalized_profile_id).parent,
                )
        return await self.get(profile_id=normalized_profile_id, file_name=resolved_name)

    async def _ensure_profile_exists(self, *, profile_id: str) -> None:
        engine = create_engine(self._settings)
        session_factory = create_session_factory(engine)
        try:
            await create_schema(engine)
            async with session_scope(session_factory) as session:
                row = await ProfileRepository(session).get(profile_id)
                if row is None:
                    raise ProfileServiceError(
                        error_code="profile_not_found",
                        reason=f"Profile not found: {profile_id}",
                    )
        finally:
            await engine.dispose()

    def _build_record(self, *, profile_id: str, file_name: str) -> ProfileBootstrapFileView:
        path = self._path(profile_id=profile_id, file_name=file_name)
        return ProfileBootstrapFileView(
            file_name=file_name,
            path=self._to_relative(path),
            exists=path.exists(),
        )

    def _path(self, *, profile_id: str, file_name: str) -> Path:
        return self._runtime_configs.bootstrap_dir(profile_id) / file_name

    def _resolve_name(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ProfileServiceError(
                error_code="profile_bootstrap_invalid_file",
                reason="Bootstrap file name is required",
            )
        by_alias: dict[str, str] = {}
        for item in self._settings.bootstrap_files:
            by_alias[item.lower()] = item
            by_alias[Path(item).stem.lower()] = item
        resolved = by_alias.get(normalized.lower())
        if resolved is None:
            allowed = ", ".join(self._settings.bootstrap_files)
            raise ProfileServiceError(
                error_code="profile_bootstrap_invalid_file",
                reason=f"Unsupported bootstrap file: {normalized}. Allowed: {allowed}",
            )
        return resolved

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve(strict=False).relative_to(root))
        except ValueError:
            return str(path.resolve(strict=False))

    @staticmethod
    def _prune_empty_dirs(*, start: Path, stop_at: Path) -> None:
        current = start
        while True:
            if current == stop_at:
                return
            try:
                current.rmdir()
            except OSError:
                return
            parent = current.parent
            if parent == current:
                return
            current = parent



def get_profile_bootstrap_service(settings: Settings) -> ProfileBootstrapService:
    """Return cached profile bootstrap service for one root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileBootstrapService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_bootstrap_services() -> None:
    """Reset cached profile bootstrap services for tests."""

    _SERVICES_BY_ROOT.clear()
