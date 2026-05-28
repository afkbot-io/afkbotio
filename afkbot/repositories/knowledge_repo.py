"""Repository for derived project knowledge rows."""

from __future__ import annotations

import json

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.dialect import session_dialect_name
from afkbot.db.upsert import upsert_insert_for_session
from afkbot.models.knowledge_artifact import KnowledgeArtifact
from afkbot.services.knowledge.contracts import (
    KnowledgeArtifactInput,
    KnowledgeArtifactMetadata,
    KnowledgeSourceRef,
)


class KnowledgeRepository:
    """Persistence operations for derived knowledge artifacts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_artifact(self, payload: KnowledgeArtifactInput) -> KnowledgeArtifact:
        """Atomically create or update a derived artifact by profile-local dedupe key."""

        source_refs_json = json.dumps(
            [item.model_dump(mode="json") for item in payload.source_refs],
            ensure_ascii=True,
            sort_keys=True,
        )
        tags_json = json.dumps(list(payload.tags), ensure_ascii=True, sort_keys=True)
        values: dict[str, object] = {
            "profile_id": payload.profile_id,
            "flow_id": payload.flow_id,
            "task_id": payload.task_id,
            "task_run_id": payload.task_run_id,
            "scope_type": payload.scope_type,
            "scope_id": payload.scope_id,
            "artifact_kind": payload.artifact_kind,
            "title": payload.title,
            "summary": payload.summary,
            "details_md": payload.details_md,
            "source_refs_json": source_refs_json,
            "tags_json": tags_json,
            "confidence": payload.confidence,
            "confirmed": payload.confirmed,
            "source_max_event_id": payload.source_max_event_id,
            "source_max_turn_id": payload.source_max_turn_id,
            "source_revision": payload.source_revision,
            "source_fingerprint": payload.source_fingerprint,
            "dedupe_key": payload.dedupe_key,
            "status": payload.status,
        }
        update_values: dict[str, object] = {
            key: value for key, value in values.items() if key != "dedupe_key"
        }
        update_values["updated_at"] = func.now()
        statement = upsert_insert_for_session(self._session, KnowledgeArtifact).values(**values)
        conflict_where = _monotonic_update_where(payload)
        dialect_name = session_dialect_name(self._session)
        if dialect_name == "postgresql":
            statement = statement.on_conflict_do_update(
                constraint="uq_knowledge_artifact_profile_dedupe",
                set_=update_values,
                where=conflict_where,
            )
        else:
            statement = statement.on_conflict_do_update(
                index_elements=["profile_id", "dedupe_key"],
                set_=update_values,
                where=conflict_where,
            )
        await self._session.execute(statement)
        await self._session.flush()
        row = await self.get_artifact_by_dedupe_key(
            profile_id=payload.profile_id,
            dedupe_key=payload.dedupe_key,
        )
        if row is None:
            raise RuntimeError("Knowledge artifact upsert did not produce a row")
        return row

    async def get_artifact_by_dedupe_key(
        self,
        *,
        profile_id: str,
        dedupe_key: str,
    ) -> KnowledgeArtifact | None:
        """Return one knowledge artifact by profile-local dedupe key."""

        statement: Select[tuple[KnowledgeArtifact]] = (
            select(KnowledgeArtifact)
            .where(
                KnowledgeArtifact.profile_id == profile_id,
                KnowledgeArtifact.dedupe_key == dedupe_key,
            )
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_artifacts_for_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        artifact_kind: str | None = None,
    ) -> list[KnowledgeArtifact]:
        """Return active artifacts derived for one task."""

        statement: Select[tuple[KnowledgeArtifact]] = select(KnowledgeArtifact).where(
            KnowledgeArtifact.profile_id == profile_id,
            KnowledgeArtifact.task_id == task_id,
            KnowledgeArtifact.status == "active",
        )
        if artifact_kind is not None:
            statement = statement.where(KnowledgeArtifact.artifact_kind == artifact_kind)
        statement = statement.order_by(
            KnowledgeArtifact.updated_at.desc(), KnowledgeArtifact.id.desc()
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def supersede_task_crystal_variants(
        self,
        *,
        profile_id: str,
        task_id: str,
        task_run_id: int | None,
        keep_dedupe_key: str,
    ) -> int:
        """Mark older task crystal key variants inactive for one task/run outcome."""

        conditions = [
            KnowledgeArtifact.profile_id == profile_id,
            KnowledgeArtifact.task_id == task_id,
            KnowledgeArtifact.artifact_kind == "task_crystal",
            KnowledgeArtifact.status == "active",
            KnowledgeArtifact.dedupe_key != keep_dedupe_key,
        ]
        if task_run_id is None:
            conditions.append(KnowledgeArtifact.task_run_id.is_(None))
        else:
            conditions.append(KnowledgeArtifact.task_run_id == task_run_id)
        statement = (
            update(KnowledgeArtifact)
            .where(*conditions)
            .values(status="superseded", updated_at=func.now())
        )
        result = await self._session.execute(statement)
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def to_artifact_metadata(row: KnowledgeArtifact) -> KnowledgeArtifactMetadata:
        """Convert one knowledge artifact row into a service contract."""

        return KnowledgeArtifactMetadata(
            id=row.id,
            profile_id=row.profile_id,
            flow_id=row.flow_id,
            task_id=row.task_id,
            task_run_id=row.task_run_id,
            scope_type=row.scope_type,
            scope_id=row.scope_id,
            artifact_kind=row.artifact_kind,
            title=row.title,
            summary=row.summary,
            details_md=row.details_md,
            source_refs=_decode_source_refs(row.source_refs_json),
            tags=tuple(str(item) for item in _decode_json_list(row.tags_json)),
            confidence=row.confidence,
            confirmed=row.confirmed,
            source_max_event_id=row.source_max_event_id,
            source_max_turn_id=row.source_max_turn_id,
            source_revision=row.source_revision,
            source_fingerprint=row.source_fingerprint,
            dedupe_key=row.dedupe_key,
            status=row.status,
        )


def _monotonic_update_where(payload: KnowledgeArtifactInput) -> object:
    """Build SQL predicate that prevents older captures from overwriting newer rows."""

    return and_(
        func.coalesce(KnowledgeArtifact.source_max_event_id, -1)
        <= _watermark(payload.source_max_event_id),
        func.coalesce(KnowledgeArtifact.source_max_turn_id, -1)
        <= _watermark(payload.source_max_turn_id),
        func.coalesce(KnowledgeArtifact.source_revision, -1)
        <= _watermark(payload.source_revision),
    )


def _watermark(value: int | None) -> int:
    return -1 if value is None else int(value)


def _decode_source_refs(raw: str) -> tuple[KnowledgeSourceRef, ...]:
    items = _decode_json_list(raw)
    refs: list[KnowledgeSourceRef] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            refs.append(KnowledgeSourceRef.model_validate(item))
        except Exception:
            continue
    return tuple(refs)


def _decode_json_list(raw: str) -> list[object]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []
