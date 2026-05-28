"""Dark-launch crystallization for completed Task Flow work."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.knowledge_repo import KnowledgeRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.knowledge.contracts import (
    KnowledgeArtifactInput,
    KnowledgeArtifactMetadata,
    KnowledgeSourceRef,
)
from afkbot.services.knowledge.policy import (
    KnowledgeActorContext,
    can_access_project_knowledge,
)
from afkbot.services.knowledge.redaction import screen_knowledge_text
from afkbot.services.task_flow.event_log import record_task_event
from afkbot.settings import Settings, get_settings

_LOGGER = logging.getLogger(__name__)
_TERMINAL_CAPTURE_STATUSES = {"completed", "review", "failed"}


@dataclass(frozen=True, slots=True)
class TaskOutcomeCrystalInput:
    """Input for crystallizing one Task Flow task outcome."""

    profile_id: str
    task_id: str
    status: str
    task_run_id: int | None = None
    summary: str | None = None
    error_code: str | None = None
    error_text: str | None = None
    actor_type: str | None = None
    actor_ref: str | None = None
    transport: str | None = None
    channel_profile: str | None = None
    occurred_at: datetime | None = None


class KnowledgeCrystallizer:
    """Create derived Task Flow knowledge artifacts after source state is persisted."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()

    async def crystallize_task_outcome(
        self,
        payload: TaskOutcomeCrystalInput,
    ) -> KnowledgeArtifactMetadata | None:
        """Create or update one task outcome crystal when enabled and safe."""

        if not self._settings.knowledge_crystals_enabled:
            return None
        if payload.status not in _TERMINAL_CAPTURE_STATUSES:
            return None

        async with session_scope(self._session_factory) as session:
            task_repo = TaskFlowRepository(session)
            knowledge_repo = KnowledgeRepository(session)
            task = await task_repo.get_task(profile_id=payload.profile_id, task_id=payload.task_id)
            if task is None:
                return None
            dedupe_key = _task_crystal_dedupe_key(
                task_id=task.id,
                task_run_id=payload.task_run_id,
            )
            if not can_access_project_knowledge(
                KnowledgeActorContext(
                    profile_id=payload.profile_id,
                    transport=payload.transport,
                    channel_profile=payload.channel_profile,
                    actor_type=payload.actor_type,
                    actor_ref=payload.actor_ref,
                ),
                target_profile_id=task.profile_id,
            ):
                if await _should_record_artifact_event(
                    task_repo,
                    task_id=task.id,
                    task_run_id=payload.task_run_id,
                    event_type="knowledge_crystallization_skipped",
                    dedupe_key=dedupe_key,
                    status=payload.status,
                ):
                    await record_task_event(
                        repo=task_repo,
                        task_id=task.id,
                        task_run_id=payload.task_run_id,
                        event_type="knowledge_crystallization_skipped",
                        actor_type=payload.actor_type or "runtime",
                        actor_ref=payload.actor_ref,
                        message="Task knowledge crystallization skipped by access policy.",
                        details={
                            "dedupe_key": dedupe_key,
                            "reason_code": "knowledge_policy_denied",
                            "transport": payload.transport,
                            "channel_profile": payload.channel_profile,
                            "status": payload.status,
                        },
                    )
                return None
            if not can_access_project_knowledge(
                KnowledgeActorContext(
                    profile_id=task.profile_id,
                    transport=task.source_transport,
                    channel_profile=task.source_channel_profile,
                    actor_type=task.created_by_type,
                    actor_ref=task.created_by_ref,
                ),
                target_profile_id=task.profile_id,
            ):
                if await _should_record_artifact_event(
                    task_repo,
                    task_id=task.id,
                    task_run_id=payload.task_run_id,
                    event_type="knowledge_crystallization_skipped",
                    dedupe_key=dedupe_key,
                    status=payload.status,
                ):
                    await record_task_event(
                        repo=task_repo,
                        task_id=task.id,
                        task_run_id=payload.task_run_id,
                        event_type="knowledge_crystallization_skipped",
                        actor_type=payload.actor_type or "runtime",
                        actor_ref=payload.actor_ref,
                        message="Task knowledge crystallization skipped by source policy.",
                        details={
                            "dedupe_key": dedupe_key,
                            "reason_code": "knowledge_source_policy_denied",
                            "source_transport": task.source_transport,
                            "source_channel_profile": task.source_channel_profile,
                            "status": payload.status,
                        },
                    )
                return None
            task_run = None
            if payload.task_run_id is not None:
                task_run = await task_repo.get_task_run(
                    task_run_id=payload.task_run_id,
                    task_id=payload.task_id,
                )
            source_max_event_id = await _latest_source_task_event_id(
                task_repo,
                task_id=payload.task_id,
            )
            summary = _outcome_summary(payload=payload, task_title=task.title)
            details = _outcome_details(
                payload=payload,
                task_description=task.description,
                task_run_summary=None if task_run is None else task_run.summary,
            )
            screened_summary = screen_knowledge_text(summary)
            screened_details = screen_knowledge_text(details)
            screened_title = screen_knowledge_text(f"Task outcome: {task.title}")
            safe_title = (
                screened_title.text
                if screened_title.allowed
                else f"Task outcome crystal: {task.id}"
            )
            if not screened_summary.allowed or not screened_details.allowed:
                reason_code = screened_summary.reason_code or screened_details.reason_code
                if await _should_record_artifact_event(
                    task_repo,
                    task_id=task.id,
                    task_run_id=payload.task_run_id,
                    event_type="knowledge_crystallization_skipped",
                    dedupe_key=dedupe_key,
                    status=payload.status,
                ):
                    await record_task_event(
                        repo=task_repo,
                        task_id=task.id,
                        task_run_id=payload.task_run_id,
                        event_type="knowledge_crystallization_skipped",
                        actor_type=payload.actor_type or "runtime",
                        actor_ref=payload.actor_ref,
                        message="Task knowledge crystallization skipped by safety filter.",
                        details={
                            "dedupe_key": dedupe_key,
                            "reason_code": reason_code or "unsafe_content",
                            "status": payload.status,
                        },
                    )
                return None

            artifact_payload = KnowledgeArtifactInput(
                profile_id=payload.profile_id,
                flow_id=task.flow_id,
                task_id=task.id,
                task_run_id=payload.task_run_id,
                scope_type="task",
                scope_id=task.id,
                artifact_kind="task_crystal",
                title=safe_title,
                summary=screened_summary.text,
                details_md=screened_details.text,
                source_refs=tuple(
                    _source_refs(task_id=task.id, task_run_id=payload.task_run_id)
                ),
                tags=("taskflow", payload.status),
                confidence=0.75 if payload.status in {"completed", "review"} else 0.55,
                confirmed=payload.status == "completed",
                source_max_event_id=source_max_event_id,
                source_revision=None if task_run is None else task_run.attempt,
                source_fingerprint=_fingerprint(
                    task.id,
                    str(payload.task_run_id or "manual"),
                    payload.status,
                    screened_summary.text,
                    screened_details.text,
                ),
                dedupe_key=dedupe_key,
                status="active",
            )
            await knowledge_repo.supersede_task_crystal_variants(
                profile_id=payload.profile_id,
                task_id=task.id,
                task_run_id=payload.task_run_id,
                keep_dedupe_key=dedupe_key,
            )
            row = await knowledge_repo.upsert_artifact(artifact_payload)
            if await _should_record_artifact_event(
                task_repo,
                task_id=task.id,
                task_run_id=payload.task_run_id,
                event_type="knowledge_crystallized",
                dedupe_key=dedupe_key,
                status=payload.status,
            ):
                await record_task_event(
                    repo=task_repo,
                    task_id=task.id,
                    task_run_id=payload.task_run_id,
                    event_type="knowledge_crystallized",
                    actor_type=payload.actor_type or "runtime",
                    actor_ref=payload.actor_ref,
                    message="Task knowledge crystal updated.",
                    details={
                        "artifact_id": row.id,
                        "artifact_kind": row.artifact_kind,
                        "dedupe_key": row.dedupe_key,
                        "status": payload.status,
                    },
                )
            return KnowledgeRepository.to_artifact_metadata(row)


async def try_crystallize_task_outcome(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    payload: TaskOutcomeCrystalInput,
) -> KnowledgeArtifactMetadata | None:
    """Run crystallization as non-critical post-processing."""

    try:
        return await KnowledgeCrystallizer(
            session_factory,
            settings=settings,
        ).crystallize_task_outcome(payload)
    except Exception:
        _LOGGER.exception(
            "knowledge_crystallization_failed profile_id=%s task_id=%s task_run_id=%s",
            payload.profile_id,
            payload.task_id,
            payload.task_run_id,
        )
        return None


async def _latest_source_task_event_id(repo: TaskFlowRepository, *, task_id: str) -> int | None:
    events = await repo.list_task_events(task_id=task_id, limit=50)
    for event in events:
        if not event.event_type.startswith("knowledge_crystall"):
            return event.id
    return None


async def _should_record_artifact_event(
    repo: TaskFlowRepository,
    *,
    task_id: str,
    task_run_id: int | None,
    event_type: str,
    dedupe_key: str,
    status: str,
) -> bool:
    if task_run_id is None:
        events = await repo.list_task_events(task_id=task_id, limit=50)
        return not any(
            event.event_type == event_type
            and _event_detail_value(event.details_json, "dedupe_key") == dedupe_key
            and _event_detail_value(event.details_json, "status") == status
            for event in events
        )
    return not await repo.has_task_run_event(task_run_id=task_run_id, event_type=event_type)


def _event_detail_value(details_json: str, key: str) -> object:
    try:
        payload = json.loads(details_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get(key)


def _task_crystal_dedupe_key(*, task_id: str, task_run_id: int | None) -> str:
    run_key = f"run:{task_run_id}" if task_run_id is not None else "manual"
    return f"task_crystal:{task_id}:{run_key}"


def _outcome_summary(*, payload: TaskOutcomeCrystalInput, task_title: str) -> str:
    if payload.summary:
        return " ".join(payload.summary.split())
    if payload.status == "failed":
        return f"Task {task_title!r} failed: {payload.error_code or 'unknown_error'}."
    return f"Task {task_title!r} reached {payload.status}."


def _outcome_details(
    *,
    payload: TaskOutcomeCrystalInput,
    task_description: str,
    task_run_summary: str | None,
) -> str:
    parts = [
        f"Status: {payload.status}",
        f"Task description: {task_description.strip()}",
    ]
    if task_run_summary:
        parts.append(f"Run summary: {task_run_summary.strip()}")
    if payload.error_code:
        parts.append(f"Error code: {payload.error_code}")
    if payload.error_text:
        parts.append(f"Error text: {payload.error_text.strip()}")
    if payload.occurred_at is not None:
        parts.append(f"Occurred at: {payload.occurred_at.isoformat()}")
    return "\n".join(parts)


def _source_refs(*, task_id: str, task_run_id: int | None) -> Sequence[KnowledgeSourceRef]:
    refs: list[KnowledgeSourceRef] = [
        KnowledgeSourceRef(source_type="task", source_id=task_id),
    ]
    if task_run_id is not None:
        refs.append(KnowledgeSourceRef(source_type="task_run", source_id=str(task_run_id)))
    return refs


def _fingerprint(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()
