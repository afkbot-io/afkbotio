"""Task Flow document workspace API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from afkbot.api.chat_auth import require_chat_http_context
from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow.contracts import (
    TaskDocumentMetadata,
    TaskDocumentRevisionMetadata,
)
from afkbot.settings import get_settings

router = APIRouter(prefix="/v1/task-documents", tags=["task-documents"])


class TaskDocumentListResponse(BaseModel):
    """Task document list response."""

    model_config = ConfigDict(extra="forbid")

    documents: list[TaskDocumentMetadata]


class TaskDocumentResponse(BaseModel):
    """Single task document response."""

    model_config = ConfigDict(extra="forbid")

    document: TaskDocumentMetadata


class TaskDocumentRevisionListResponse(BaseModel):
    """Document revision list response."""

    model_config = ConfigDict(extra="forbid")

    revisions: list[TaskDocumentRevisionMetadata]


class TaskDocumentConfirmRequest(BaseModel):
    """Confirm-document request payload."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)


@router.get("/", response_model=TaskDocumentListResponse)
async def get_task_documents(
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
    scope_type: str | None = Query(default=None, min_length=1),
    scope_id: str | None = Query(default=None, min_length=1),
    document_key: str | None = Query(default=None, min_length=1),
    confirmation_status: str | None = Query(default=None, min_length=1),
    query: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskDocumentListResponse:
    """List Task Flow documents visible to the authenticated profile."""

    auth_context = await _require_profile_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
    )
    service = get_task_flow_service(get_settings())
    try:
        documents = await service.list_documents(
            profile_id=auth_context.profile_id,
            scope_type=scope_type,
            scope_id=scope_id,
            document_key=document_key,
            confirmation_status=confirmation_status,
            query=query,
            limit=limit,
            offset=offset,
        )
    except TaskFlowServiceError as exc:
        raise _task_document_http_error(exc) from exc
    return TaskDocumentListResponse(documents=documents)


@router.get("/{document_id}", response_model=TaskDocumentResponse)
async def get_task_document(
    document_id: str,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
) -> TaskDocumentResponse:
    """Return one Task Flow document by id."""

    auth_context = await _require_profile_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
    )
    service = get_task_flow_service(get_settings())
    try:
        document = await service.get_document(
            profile_id=auth_context.profile_id,
            document_id=document_id,
        )
    except TaskFlowServiceError as exc:
        raise _task_document_http_error(exc) from exc
    return TaskDocumentResponse(document=document)


@router.get("/{document_id}/revisions", response_model=TaskDocumentRevisionListResponse)
async def get_task_document_revisions(
    document_id: str,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> TaskDocumentRevisionListResponse:
    """Return immutable revisions for one Task Flow document."""

    auth_context = await _require_profile_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
    )
    service = get_task_flow_service(get_settings())
    try:
        revisions = await service.list_document_revisions(
            profile_id=auth_context.profile_id,
            document_id=document_id,
            limit=limit,
        )
    except TaskFlowServiceError as exc:
        raise _task_document_http_error(exc) from exc
    return TaskDocumentRevisionListResponse(revisions=revisions)


@router.post("/{document_id}/confirm", response_model=TaskDocumentResponse)
async def post_task_document_confirm(
    document_id: str,
    request: TaskDocumentConfirmRequest,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
) -> TaskDocumentResponse:
    """Confirm the current revision of one Task Flow document."""

    auth_context = await _require_profile_context(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
    )
    service = get_task_flow_service(get_settings())
    try:
        document = await service.confirm_document(
            profile_id=auth_context.profile_id,
            document_id=document_id,
            actor_type="human",
            actor_ref=f"api:{auth_context.session_id}",
            actor_session_id=auth_context.session_id,
            expected_revision=request.expected_revision,
        )
    except TaskFlowServiceError as exc:
        raise _task_document_http_error(exc) from exc
    return TaskDocumentResponse(document=document)


async def _require_profile_context(
    *,
    authorization: str | None,
    session_proof: str | None,
    profile_id: str | None,
):
    auth_context = await require_chat_http_context(
        authorization=authorization,
        session_proof=session_proof,
    )
    if profile_id is not None and profile_id != auth_context.profile_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "ok": False,
                "error_code": "task_document_profile_scope_mismatch",
                "reason": "Requested profile does not match the access token profile.",
            },
        )
    return auth_context


def _task_document_http_error(exc: TaskFlowServiceError) -> HTTPException:
    return HTTPException(
        status_code=_task_document_status_code(exc.error_code),
        detail={
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
        },
    )


def _task_document_status_code(error_code: str) -> int:
    if error_code in {"profile_not_found", "task_document_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if error_code in {"task_document_revision_conflict"}:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


__all__ = [
    "TaskDocumentConfirmRequest",
    "TaskDocumentListResponse",
    "TaskDocumentResponse",
    "TaskDocumentRevisionListResponse",
    "router",
]
