
"""Shared harness helpers for channel CLI tests."""

from pathlib import Path

from pytest import MonkeyPatch

from afkbot.services.profile_runtime.service import ProfileService
from afkbot.settings import get_settings

_OWNED_PROFILE_SERVICES: list[ProfileService] = []


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'channels.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", "5lSxJmWfyATJQkFFUXMPaZTTHm62LvPNtvBI3AmyuKY=")
    get_settings.cache_clear()

def _new_profile_service(settings: object) -> ProfileService:
    service = ProfileService(settings)  # type: ignore[arg-type]
    _OWNED_PROFILE_SERVICES.append(service)
    return service

async def _reset_owned_profile_services_async() -> None:
    while _OWNED_PROFILE_SERVICES:
        service = _OWNED_PROFILE_SERVICES.pop()
        await service.shutdown()
