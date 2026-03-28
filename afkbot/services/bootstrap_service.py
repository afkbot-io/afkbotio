"""CRUD service for global bootstrap/system-prompt markdown files."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from afkbot.services.atomic_writes import atomic_text_write
from afkbot.settings import Settings

_GLOBAL_BOOTSTRAP_FALLBACKS: dict[str, str] = {
    "AGENTS.md": "Follow the repository AGENTS.md and the current user request.",
    "IDENTITY.md": "Use the default AFKBOT system identity for this runtime.",
    "TOOLS.md": "Use the configured tool permissions and runtime policy.",
    "SECURITY.md": "Protect secrets, preserve explicit user intent, and avoid unsafe side effects.",
}
_GENERIC_GLOBAL_BOOTSTRAP_FALLBACK = "Use the default AFKBOT bootstrap guidance for this file."


class BootstrapServiceError(ValueError):
    """Structured bootstrap service error surfaced by CLI callers."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class BootstrapFileView(BaseModel):
    """Summary view for one global bootstrap slot."""

    file_name: str
    path: str
    exists: bool = False


class BootstrapRecord(BootstrapFileView):
    """Detailed view for one global bootstrap file."""

    content: str | None = None


class BootstrapService:
    """Manage global bootstrap files under the configured workspace bootstrap directory."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list(self) -> list[BootstrapFileView]:
        """List configured global bootstrap slots."""

        return [
            BootstrapFileView(
                file_name=file_name,
                path=self._to_relative(self._path(file_name)),
                exists=self._path(file_name).exists(),
            )
            for file_name in self._settings.bootstrap_files
        ]

    def get(self, *, file_name: str) -> BootstrapRecord:
        """Return one global bootstrap file record."""

        resolved_name = self._resolve_name(file_name)
        path = self._path(resolved_name)
        return BootstrapRecord(
            file_name=resolved_name,
            path=self._to_relative(path),
            exists=path.exists(),
            content=path.read_text(encoding="utf-8") if path.exists() else None,
        )

    def write(self, *, file_name: str, content: str) -> BootstrapRecord:
        """Create or replace one global bootstrap file."""

        resolved_name = self._resolve_name(file_name)
        normalized_content = content.strip()
        if not normalized_content:
            raise BootstrapServiceError(
                error_code="bootstrap_empty",
                reason="Bootstrap content is required",
            )
        path = self._path(resolved_name)
        atomic_text_write(path, normalized_content + "\n", mode=0o600)
        return self.get(file_name=resolved_name)

    def _path(self, file_name: str) -> Path:
        return self._settings.bootstrap_dir / file_name

    def _resolve_name(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise BootstrapServiceError(
                error_code="bootstrap_invalid_file",
                reason="Bootstrap file name is required",
            )
        by_alias: dict[str, str] = {}
        for item in self._settings.bootstrap_files:
            by_alias[item.lower()] = item
            by_alias[Path(item).stem.lower()] = item
        resolved = by_alias.get(normalized.lower())
        if resolved is None:
            allowed = ", ".join(self._settings.bootstrap_files)
            raise BootstrapServiceError(
                error_code="bootstrap_invalid_file",
                reason=f"Unsupported bootstrap file: {normalized}. Allowed: {allowed}",
            )
        return resolved

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve(strict=False).relative_to(root))
        except ValueError:
            return str(path.resolve(strict=False))


def seed_missing_global_bootstrap_files(settings: Settings) -> tuple[Path, ...]:
    """Create any missing global bootstrap files for a fresh runtime root."""

    template_dir = Path(__file__).resolve().parents[1] / "bootstrap"
    created: list[Path] = []
    for file_name in settings.bootstrap_files:
        target_path = settings.bootstrap_dir / file_name
        if target_path.exists():
            continue
        template_path = template_dir / file_name
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8").strip()
        else:
            content = _GLOBAL_BOOTSTRAP_FALLBACKS.get(
                file_name,
                _GENERIC_GLOBAL_BOOTSTRAP_FALLBACK,
            )
        atomic_text_write(target_path, content.rstrip() + "\n", mode=0o600)
        created.append(target_path)
    return tuple(created)
