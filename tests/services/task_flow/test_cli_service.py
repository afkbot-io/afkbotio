"""Tests for Task Flow CLI payload helpers."""

from __future__ import annotations

import json

from afkbot.services.error_logging import component_log_path
from afkbot.services.task_flow import cli_service
from afkbot.settings import get_settings


class _ExplodingTaskFlowService:
    async def create_task(self, **_: object) -> object:
        raise RuntimeError("task token=secret")


async def test_create_task_payload_logs_unexpected_exception(tmp_path, monkeypatch) -> None:
    """Unexpected task-create failures should return a stable payload and write a traceback."""

    async def _profile_exists(*_: object, **__: object) -> None:
        return None

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(cli_service, "_ensure_profile_exists", _profile_exists)
    monkeypatch.setattr(
        cli_service,
        "get_task_flow_service",
        lambda _settings: _ExplodingTaskFlowService(),
    )

    payload = await cli_service.create_task_payload(
        profile_id="default",
        title="Demo",
        created_by_type="human",
        created_by_ref="cli",
    )

    get_settings.cache_clear()
    data = json.loads(payload)
    assert data == {
        "ok": False,
        "error_code": "task_create_failed",
        "reason": "Task creation failed. Run `afk logs` to find the diagnostic log path.",
    }
    contents = component_log_path(get_settings(), "taskflow").read_text(encoding="utf-8")
    assert "Unhandled task create CLI exception" in contents
    assert "profile_id=default" in contents
    assert "RuntimeError: task token=[REDACTED]" in contents
    assert "secret" not in contents


async def test_create_task_payload_logs_schema_bootstrap_exception(tmp_path, monkeypatch) -> None:
    """Schema/bootstrap failures should use the same stable task-create fallback."""

    async def _explode_schema(_engine: object) -> None:
        raise RuntimeError("schema password=secret")

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(cli_service, "create_schema", _explode_schema)

    payload = await cli_service.create_task_payload(
        profile_id="default",
        title="Demo",
        created_by_type="human",
        created_by_ref="cli",
    )

    get_settings.cache_clear()
    data = json.loads(payload)
    assert data == {
        "ok": False,
        "error_code": "task_create_failed",
        "reason": "Task creation failed. Run `afk logs` to find the diagnostic log path.",
    }
    contents = component_log_path(get_settings(), "taskflow").read_text(encoding="utf-8")
    assert "Unhandled task create CLI exception" in contents
    assert "RuntimeError: schema password=[REDACTED]" in contents
    assert "secret" not in contents
