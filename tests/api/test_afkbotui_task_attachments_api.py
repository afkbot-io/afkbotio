"""Tests for AFKBOT UI Task Flow attachment routes."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from afkbot.services.task_flow.contracts import (
    TaskAttachmentContent,
    TaskAttachmentCreate,
    TaskAttachmentMetadata,
)
from afkbot.settings import get_settings

_PLUGIN_PYTHON_ROOT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "packages"
    / "afkbotui"
    / "0.3.3"
    / "python"
)
if str(_PLUGIN_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PYTHON_ROOT))

from afkbot_plugin_afkbotui import router as afkbotui_router  # noqa: E402


def _attachment(**overrides: object) -> TaskAttachmentMetadata:
    values = {
        "id": "att_1",
        "task_id": "task_1",
        "profile_id": "default",
        "name": 'evidence"; bad.txt',
        "content_type": "text/plain",
        "kind": "file",
        "byte_size": 11,
        "sha256": "sha256-test",
        "created_by_type": "human",
        "created_by_ref": "web-user",
        "created_at": datetime(2026, 6, 24, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 24, tzinfo=UTC),
    }
    values.update(overrides)
    return TaskAttachmentMetadata.model_validate(values)


def test_afkbotui_task_attachment_routes_cover_upload_download_and_delete(monkeypatch) -> None:
    """Task Flow UI API should expose file attachment lifecycle for one task."""

    monkeypatch.setenv("AFKBOT_CHAT_HUMAN_OWNER_REF", "web-user")
    get_settings.cache_clear()
    calls: dict[str, object] = {}
    attachment = _attachment()

    class _Service:
        async def list_task_attachments(self, **kwargs: object) -> list[TaskAttachmentMetadata]:
            calls["list"] = kwargs
            return [attachment]

        async def add_task_attachment(
            self,
            **kwargs: object,
        ) -> TaskAttachmentMetadata:
            calls["add"] = kwargs
            assert isinstance(kwargs["attachment"], TaskAttachmentCreate)
            return attachment

        async def get_task_attachment_content(self, **kwargs: object) -> TaskAttachmentContent:
            calls["download"] = kwargs
            return TaskAttachmentContent(attachment=attachment, content_bytes=b"hello world")

        async def remove_task_attachment(self, **kwargs: object) -> None:
            calls["delete"] = kwargs

    monkeypatch.setattr(afkbotui_router, "get_task_flow_service", lambda _settings: _Service())

    class _Registry:
        def read_config(self) -> dict[str, object]:
            return {}

    app = FastAPI()
    app.include_router(
        afkbotui_router.build_router(api_prefix="/v1/plugins/afkbotui", registry=_Registry())
    )
    client = TestClient(app)

    listed = client.get("/v1/plugins/afkbotui/task-flow/tasks/task_1/attachments")
    added = client.post(
        "/v1/plugins/afkbotui/task-flow/tasks/task_1/attachments",
        json={
            "actor_type": "human",
            "actor_ref": "web-user",
            "attachments": [
                {
                    "name": "evidence.txt",
                    "content_base64": "aGVsbG8gd29ybGQ=",
                    "content_type": "text/plain",
                    "kind": "file",
                }
            ],
        },
    )
    downloaded = client.get(
        "/v1/plugins/afkbotui/task-flow/tasks/task_1/attachments/att_1/download"
    )
    deleted = client.request(
        "DELETE",
        "/v1/plugins/afkbotui/task-flow/tasks/task_1/attachments/att_1",
        json={"actor_type": "human", "actor_ref": "web-user"},
    )

    assert listed.status_code == 200
    assert listed.json()["task_attachments"][0]["id"] == "att_1"
    assert added.status_code == 200
    assert added.json()["task_attachments"][0]["name"] == 'evidence"; bad.txt'
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello world"
    assert downloaded.headers["content-disposition"] == (
        'attachment; filename="evidence__ bad.txt"'
    )
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "attachment_id": "att_1"}
    assert calls["list"] == {"profile_id": "default", "task_id": "task_1"}
    assert calls["add"]["actor_type"] == "human"
    assert calls["add"]["actor_ref"] == "web-user"
    assert calls["delete"]["actor_type"] == "human"
    assert calls["delete"]["actor_ref"] == "web-user"
