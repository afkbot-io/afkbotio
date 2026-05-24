"""Profile-scoped Task Flow team roster storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings

TASKFLOW_TEAM_CONFIG_VERSION = 1
_TASKFLOW_TEAM_CONFIG_FILENAME = "taskflow_team.json"
_SERVICES_BY_ROOT: dict[str, "TaskFlowTeamConfigService"] = {}


class TaskFlowTeamConfigService:
    """Read and write the AI teammate roster for one Task Flow backlog profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def config_path(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile team roster file."""

        validate_profile_id(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        profile_root = (profiles_root / profile_id).resolve()
        if not profile_root.is_relative_to(profiles_root):
            raise ValueError(f"Invalid profile root: {profile_id}")
        return profile_root / ".system" / _TASKFLOW_TEAM_CONFIG_FILENAME

    def load(self, profile_id: str) -> tuple[str, ...] | None:
        """Load a stored team roster, or return None when no explicit roster exists."""

        path = self.config_path(profile_id)
        if not path.exists():
            return None
        payload = self._read_json_object(path)
        if payload.get("version") != TASKFLOW_TEAM_CONFIG_VERSION:
            raise ValueError(f"Unsupported Task Flow team config version in {path}")
        raw_team_ids = payload.get("taskflow_team_profile_ids")
        if raw_team_ids is None:
            return ()
        if not isinstance(raw_team_ids, list):
            raise ValueError(f"Invalid Task Flow team config payload in {path}")
        return _normalize_team_profile_ids(raw_team_ids)

    def write(self, profile_id: str, team_profile_ids: tuple[str, ...]) -> Path:
        """Persist one team roster without materializing profile runtime overrides."""

        path = self.config_path(profile_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": TASKFLOW_TEAM_CONFIG_VERSION,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "taskflow_team_profile_ids": list(_normalize_team_profile_ids(team_profile_ids)),
        }
        atomic_json_write(path, payload, mode=0o600)
        return path

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid JSON object in {path}")
        return payload


def _normalize_team_profile_ids(values: tuple[str, ...] | list[Any]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        profile_id = str(item or "").strip()
        if not profile_id or profile_id in seen:
            continue
        validate_profile_id(profile_id)
        seen.add(profile_id)
        normalized.append(profile_id)
    return tuple(normalized)


def get_taskflow_team_config_service(settings: Settings) -> TaskFlowTeamConfigService:
    """Return cached Task Flow team config service for one root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = TaskFlowTeamConfigService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_taskflow_team_config_services() -> None:
    """Reset cached Task Flow team config services for tests."""

    _SERVICES_BY_ROOT.clear()
