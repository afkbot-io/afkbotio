"""Tests for project knowledge repository persistence."""

from __future__ import annotations

from pathlib import Path

from afkbot.db.session import session_scope
from afkbot.repositories.knowledge_repo import KnowledgeRepository
from afkbot.services.knowledge.contracts import KnowledgeArtifactInput, KnowledgeSourceRef
from tests.repositories._harness import build_repository_factory


async def test_knowledge_repo_upserts_artifact_by_dedupe_key_and_watermark(
    tmp_path: Path,
) -> None:
    """Knowledge artifacts should be idempotent and monotonic by source watermark."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_repo.db",
        profile_ids=("default",),
    )
    try:
        async with session_scope(factory) as session:
            repo = KnowledgeRepository(session)
            first = await repo.upsert_artifact(
                KnowledgeArtifactInput(
                    profile_id="default",
                    flow_id="flow-1",
                    task_id="task-1",
                    task_run_id=1,
                    scope_type="task",
                    scope_id="task-1",
                    artifact_kind="task_crystal",
                    title="Task outcome",
                    summary="Initial summary",
                    details_md="Initial details",
                    source_refs=(KnowledgeSourceRef(source_type="task", source_id="task-1"),),
                    tags=("taskflow", "completed"),
                    confidence=0.75,
                    confirmed=True,
                    source_max_event_id=10,
                    source_revision=1,
                    source_fingerprint="a" * 64,
                    dedupe_key="task_crystal:task-1:run:1:completed",
                )
            )
            stale = await repo.upsert_artifact(
                KnowledgeArtifactInput(
                    profile_id="default",
                    flow_id="flow-1",
                    task_id="task-1",
                    task_run_id=1,
                    scope_type="task",
                    scope_id="task-1",
                    artifact_kind="task_crystal",
                    title="Task outcome",
                    summary="Stale summary",
                    details_md="Stale details",
                    source_refs=(KnowledgeSourceRef(source_type="task", source_id="task-1"),),
                    source_max_event_id=9,
                    source_revision=1,
                    source_fingerprint="b" * 64,
                    dedupe_key="task_crystal:task-1:run:1:completed",
                )
            )
            assert first.id == stale.id
            assert stale.summary == "Initial summary"
            fresh = await repo.upsert_artifact(
                KnowledgeArtifactInput(
                    profile_id="default",
                    flow_id="flow-1",
                    task_id="task-1",
                    task_run_id=1,
                    scope_type="task",
                    scope_id="task-1",
                    artifact_kind="task_crystal",
                    title="Task outcome",
                    summary="Fresh summary",
                    details_md="Fresh details",
                    source_refs=(KnowledgeSourceRef(source_type="task", source_id="task-1"),),
                    source_max_event_id=11,
                    source_revision=1,
                    source_fingerprint="c" * 64,
                    dedupe_key="task_crystal:task-1:run:1:completed",
                )
            )

            assert first.id == fresh.id
            assert fresh.summary == "Fresh summary"
            assert fresh.source_max_event_id == 11
    finally:
        await engine.dispose()


async def test_knowledge_repo_lists_active_artifacts_for_task(tmp_path: Path) -> None:
    """Task reads should stay scoped to profile, task, status, and optional kind."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_repo_list.db",
        profile_ids=("default", "other"),
    )
    try:
        async with session_scope(factory) as session:
            repo = KnowledgeRepository(session)
            for profile_id, task_id, status in (
                ("default", "task-1", "active"),
                ("default", "task-1", "stale"),
                ("default", "task-2", "active"),
                ("other", "task-1", "active"),
            ):
                await repo.upsert_artifact(
                    KnowledgeArtifactInput(
                        profile_id=profile_id,
                        flow_id="flow-1",
                        task_id=task_id,
                        task_run_id=1,
                        scope_type="task",
                        scope_id=task_id,
                        artifact_kind="task_crystal",
                        title=f"{profile_id}:{task_id}:{status}",
                        summary=f"{profile_id}:{task_id}:{status}",
                        source_refs=(
                            KnowledgeSourceRef(source_type="task", source_id=task_id),
                        ),
                        source_max_event_id=1,
                        source_fingerprint=f"{profile_id}:{task_id}:{status}",
                        dedupe_key=f"{profile_id}:{task_id}:{status}",
                        status=status,
                    )
                )

            rows = await repo.list_artifacts_for_task(
                profile_id="default",
                task_id="task-1",
                artifact_kind="task_crystal",
            )

            assert [row.summary for row in rows] == ["default:task-1:active"]
    finally:
        await engine.dispose()


async def test_knowledge_repo_supersedes_legacy_task_crystal_key_variants(
    tmp_path: Path,
) -> None:
    """Changing task crystal dedupe shape should not leave old active rows visible."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_repo_supersede_legacy.db",
        profile_ids=("default",),
    )
    try:
        async with session_scope(factory) as session:
            repo = KnowledgeRepository(session)
            for status in ("review", "completed"):
                await repo.upsert_artifact(
                    KnowledgeArtifactInput(
                        profile_id="default",
                        flow_id="flow-1",
                        task_id="task-1",
                        task_run_id=None,
                        scope_type="task",
                        scope_id="task-1",
                        artifact_kind="task_crystal",
                        title=f"Legacy {status}",
                        summary=f"Legacy {status}",
                        source_refs=(
                            KnowledgeSourceRef(source_type="task", source_id="task-1"),
                        ),
                        source_max_event_id=1,
                        source_fingerprint=f"legacy:{status}",
                        dedupe_key=f"task_crystal:task-1:manual:{status}",
                    )
                )

            superseded = await repo.supersede_task_crystal_variants(
                profile_id="default",
                task_id="task-1",
                task_run_id=None,
                keep_dedupe_key="task_crystal:task-1:manual",
            )
            await repo.upsert_artifact(
                KnowledgeArtifactInput(
                    profile_id="default",
                    flow_id="flow-1",
                    task_id="task-1",
                    task_run_id=None,
                    scope_type="task",
                    scope_id="task-1",
                    artifact_kind="task_crystal",
                    title="Current",
                    summary="Current",
                    source_refs=(KnowledgeSourceRef(source_type="task", source_id="task-1"),),
                    source_max_event_id=2,
                    source_fingerprint="current",
                    dedupe_key="task_crystal:task-1:manual",
                )
            )

            active_rows = await repo.list_artifacts_for_task(
                profile_id="default",
                task_id="task-1",
                artifact_kind="task_crystal",
            )
            old_review = await repo.get_artifact_by_dedupe_key(
                profile_id="default",
                dedupe_key="task_crystal:task-1:manual:review",
            )
            old_completed = await repo.get_artifact_by_dedupe_key(
                profile_id="default",
                dedupe_key="task_crystal:task-1:manual:completed",
            )

        assert superseded == 2
        assert [row.dedupe_key for row in active_rows] == ["task_crystal:task-1:manual"]
        assert old_review is not None
        assert old_review.status == "superseded"
        assert old_completed is not None
        assert old_completed.status == "superseded"
    finally:
        await engine.dispose()
