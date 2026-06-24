"""Tests for Task Flow document HTTP API routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.task_flow.contracts import (
    TaskDocumentMetadata,
    TaskDocumentRevisionMetadata,
)
from afkbot.services.task_flow.errors import TaskFlowServiceError
from afkbot.settings import get_settings
from tests.api.chat_api._harness import auth_headers, patch_valid_chat_access_token


def _document(**overrides: object) -> TaskDocumentMetadata:
    values = {
        "id": "doc_1",
        "profile_id": "default",
        "scope_type": "task",
        "scope_id": "task_1",
        "document_key": "handoff",
        "title": "Launch handoff",
        "body": "Release notes and next steps.",
        "revision": 2,
        "confirmation_status": "draft",
        "confirmed_revision": None,
        "confirmed_by_type": None,
        "confirmed_by_ref": None,
        "confirmed_at": None,
        "latest_revision_id": 11,
        "created_by_type": "employee",
        "created_by_ref": "default",
        "updated_by_type": "employee",
        "updated_by_ref": "default",
        "created_at": datetime(2026, 5, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 26, tzinfo=UTC),
    }
    values.update(overrides)
    return TaskDocumentMetadata.model_validate(values)


def _revision(**overrides: object) -> TaskDocumentRevisionMetadata:
    values = {
        "id": 11,
        "document_id": "doc_1",
        "revision": 2,
        "title": "Launch handoff",
        "body": "Release notes and next steps.",
        "created_by_type": "employee",
        "created_by_ref": "default",
        "created_at": datetime(2026, 5, 26, tzinfo=UTC),
    }
    values.update(overrides)
    return TaskDocumentRevisionMetadata.model_validate(values)


def test_task_documents_route_lists_documents_with_filters(monkeypatch: MonkeyPatch) -> None:
    """GET /v1/task-documents should expose searchable profile-scoped document metadata."""

    patch_valid_chat_access_token(monkeypatch, allow_operator_workspace=True)
    calls: dict[str, object] = {}

    class _Service:
        async def list_documents(self, **kwargs: object) -> list[TaskDocumentMetadata]:
            calls.update(kwargs)
            return [_document()]

    monkeypatch.setattr("afkbot.api.routes_task_documents.get_task_flow_service", lambda _settings: _Service())
    client = TestClient(create_app())

    response = client.get(
        "/v1/task-documents?query=release&scope_type=task&confirmation_status=draft&limit=25&offset=5",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["documents"][0]["id"] == "doc_1"
    assert calls == {
        "confirmation_status": "draft",
        "document_key": None,
        "limit": 25,
        "offset": 5,
        "profile_id": "default",
        "query": "release",
        "scope_id": None,
        "scope_type": "task",
    }


def test_task_documents_route_reads_detail_revisions_and_confirms(monkeypatch: MonkeyPatch) -> None:
    """Document routes should share one service contract for detail, history, and confirmation."""

    monkeypatch.setenv("AFKBOT_CHAT_HUMAN_OWNER_REF", "web-user")
    get_settings.cache_clear()
    patch_valid_chat_access_token(
        monkeypatch,
        session_id="ui-session",
        allow_operator_workspace=True,
    )
    calls: dict[str, object] = {}

    class _Service:
        async def get_document(self, **kwargs: object) -> TaskDocumentMetadata:
            calls["get"] = kwargs
            return _document()

        async def list_document_revisions(self, **kwargs: object) -> list[TaskDocumentRevisionMetadata]:
            calls["revisions"] = kwargs
            return [_revision()]

        async def confirm_document(self, **kwargs: object) -> TaskDocumentMetadata:
            calls["confirm"] = kwargs
            return _document(confirmation_status="confirmed", confirmed_revision=2)

        async def delete_document(self, **kwargs: object) -> TaskDocumentMetadata:
            calls["delete"] = kwargs
            return _document()

    monkeypatch.setattr("afkbot.api.routes_task_documents.get_task_flow_service", lambda _settings: _Service())
    client = TestClient(create_app())

    detail = client.get("/v1/task-documents/doc_1", headers=auth_headers())
    revisions = client.get("/v1/task-documents/doc_1/revisions?limit=3", headers=auth_headers())
    confirmed = client.post(
        "/v1/task-documents/doc_1/confirm",
        json={"expected_revision": 2},
        headers=auth_headers(),
    )
    deleted = client.request(
        "DELETE",
        "/v1/task-documents/doc_1",
        json={"expected_revision": 2},
        headers=auth_headers(),
    )

    assert detail.status_code == 200
    assert revisions.status_code == 200
    assert confirmed.status_code == 200
    assert deleted.status_code == 200
    assert revisions.json()["revisions"][0]["revision"] == 2
    assert confirmed.json()["document"]["confirmation_status"] == "confirmed"
    assert deleted.json()["document"]["id"] == "doc_1"
    assert calls["get"] == {"document_id": "doc_1", "profile_id": "default"}
    assert calls["revisions"] == {"document_id": "doc_1", "limit": 3, "profile_id": "default"}
    assert calls["confirm"] == {
        "actor_ref": "connect:default:ui-session",
        "actor_session_id": "ui-session",
        "actor_type": "operator",
        "document_id": "doc_1",
        "expected_revision": 2,
        "profile_id": "default",
    }
    assert calls["delete"] == {
        "actor_ref": "connect:default:ui-session",
        "actor_session_id": "ui-session",
        "actor_type": "operator",
        "document_id": "doc_1",
        "expected_revision": 2,
        "profile_id": "default",
    }
    get_settings.cache_clear()


def test_task_documents_routes_require_operator_scope(monkeypatch: MonkeyPatch) -> None:
    """Ordinary chat-scoped connect tokens must not access the operator document workspace."""

    patch_valid_chat_access_token(monkeypatch, allow_diagnostics=False)

    class _Service:
        async def list_documents(self, **_kwargs: object) -> list[TaskDocumentMetadata]:
            return [_document()]

        async def get_document(self, **_kwargs: object) -> TaskDocumentMetadata:
            return _document()

        async def list_document_revisions(self, **_kwargs: object) -> list[TaskDocumentRevisionMetadata]:
            return [_revision()]

        async def confirm_document(self, **_kwargs: object) -> TaskDocumentMetadata:
            return _document(confirmation_status="confirmed", confirmed_revision=2)

        async def delete_document(self, **_kwargs: object) -> TaskDocumentMetadata:
            return _document()

    monkeypatch.setattr("afkbot.api.routes_task_documents.get_task_flow_service", lambda _settings: _Service())
    client = TestClient(create_app())

    responses = [
        client.get("/v1/task-documents", headers=auth_headers()),
        client.get("/v1/task-documents/doc_1", headers=auth_headers()),
        client.get("/v1/task-documents/doc_1/revisions", headers=auth_headers()),
        client.post(
            "/v1/task-documents/doc_1/confirm",
            json={"expected_revision": 2},
            headers=auth_headers(),
        ),
        client.request(
            "DELETE",
            "/v1/task-documents/doc_1",
            json={"expected_revision": 2},
            headers=auth_headers(),
        ),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json() == {
            "detail": {
                "ok": False,
                "error_code": "connect_operator_scope_required",
                "reason": "Access token is not allowed to use operator Task Flow document workspace.",
            }
        }


def test_task_documents_routes_reject_diagnostics_only_scope(monkeypatch: MonkeyPatch) -> None:
    """Diagnostics access must not imply Task Flow document workspace authority."""

    patch_valid_chat_access_token(
        monkeypatch,
        allow_diagnostics=True,
        allow_operator_workspace=False,
    )

    class _Service:
        async def list_documents(self, **_kwargs: object) -> list[TaskDocumentMetadata]:
            return [_document()]

    monkeypatch.setattr("afkbot.api.routes_task_documents.get_task_flow_service", lambda _settings: _Service())
    client = TestClient(create_app())

    response = client.get("/v1/task-documents", headers=auth_headers())

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "connect_operator_scope_required",
            "reason": "Access token is not allowed to use operator Task Flow document workspace.",
        }
    }


def test_task_documents_route_maps_service_errors(monkeypatch: MonkeyPatch) -> None:
    """Task document service errors should become deterministic HTTP payloads."""

    patch_valid_chat_access_token(monkeypatch, allow_operator_workspace=True)

    class _Service:
        async def get_document(self, **_kwargs: object) -> TaskDocumentMetadata:
            raise TaskFlowServiceError(
                error_code="task_document_not_found",
                reason="Task Flow document not found",
            )

    monkeypatch.setattr("afkbot.api.routes_task_documents.get_task_flow_service", lambda _settings: _Service())
    client = TestClient(create_app())

    response = client.get("/v1/task-documents/missing", headers=auth_headers())

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "ok": False,
            "error_code": "task_document_not_found",
            "reason": "Task Flow document not found",
        }
    }
