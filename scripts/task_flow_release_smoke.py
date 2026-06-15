"""Deterministic smoke check for Task Flow release readiness."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.task_flow.service import TaskFlowService
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.settings import Settings


def _write_employee(
    *,
    settings: Settings,
    profile_id: str,
    employee_id: str,
    name: str,
    title: str,
    manager_id: str | None = None,
) -> None:
    employees_dir = settings.profiles_dir / profile_id / "employees"
    employees_dir.mkdir(parents=True, exist_ok=True)
    manager_line = f"manager_id: {manager_id}\n" if manager_id else ""
    (employees_dir / f"{employee_id}.md").write_text(
        "---\n"
        f"id: {employee_id}\n"
        f"name: {name}\n"
        f"title: {title}\n"
        "role: release-smoke\n"
        "status: active\n"
        f"{manager_line}"
        "allowed_tools:\n"
        "  - task.*\n"
        "max_active_tasks: 1\n"
        "---\n\n"
        f"{name} participates in the deterministic Task Flow release smoke.\n",
        encoding="utf-8",
    )


async def _run_smoke() -> dict[str, object]:
    with TemporaryDirectory(prefix="afkbot-taskflow-smoke-") as root_dir_str:
        root_dir = Path(root_dir_str)
        db_path = root_dir / "taskflow_smoke.db"
        settings = Settings(
            root_dir=root_dir,
            db_url=f"sqlite+aiosqlite:///{db_path}",
            taskflow_runtime_poll_interval_sec=0.01,
            taskflow_runtime_claim_ttl_sec=60,
            taskflow_public_principal_required=False,
        )
        engine = create_engine(settings)
        await create_schema(engine)
        session_factory = create_session_factory(engine)
        service = TaskFlowService(session_factory, settings=settings)
        runtime = TaskFlowRuntimeService(settings=settings, session_factory=session_factory)
        try:
            async with session_scope(session_factory) as session:
                profiles = ProfileRepository(session)
                await profiles.get_or_create_default("default")
            _write_employee(
                settings=settings,
                profile_id="default",
                employee_id="cto",
                name="CTO",
                title="Root employee",
            )
            _write_employee(
                settings=settings,
                profile_id="default",
                employee_id="analyst",
                name="Analyst",
                title="Analysis employee",
                manager_id="cto",
            )
            _write_employee(
                settings=settings,
                profile_id="default",
                employee_id="reviewer",
                name="Reviewer",
                title="Review employee",
                manager_id="cto",
            )

            flow = await service.create_flow(
                profile_id="default",
                title="Release smoke",
                description="Deterministic Task Flow smoke scenario.",
                created_by_type="human",
                created_by_ref="smoke",
                default_owner_type="employee",
                default_owner_ref="cto",
                labels=("release-smoke",),
            )

            prep = await service.create_task(
                profile_id="default",
                flow_id=flow.id,
                title="Prepare analysis",
                description="Prepare the release analysis.",
                created_by_type="human",
                created_by_ref="smoke",
                owner_type="employee",
                owner_ref="analyst",
            )
            publish = await service.create_task(
                profile_id="default",
                flow_id=flow.id,
                title="Publish result",
                description="Publish after analysis is complete.",
                created_by_type="human",
                created_by_ref="smoke",
                owner_type="employee",
                owner_ref="cto",
                depends_on_task_ids=(prep.id,),
            )
            await service.update_task(
                profile_id="default",
                task_id=prep.id,
                status="completed",
                actor_type="human",
                actor_ref="smoke",
            )
            publish_after = await service.get_task(profile_id="default", task_id=publish.id)

            review_task = await service.create_task(
                profile_id="default",
                title="Review draft",
                description="Review the generated draft.",
                created_by_type="human",
                created_by_ref="smoke",
                owner_type="employee",
                owner_ref="analyst",
                reviewer_type="employee",
                reviewer_ref="reviewer",
            )
            await service.update_task(
                profile_id="default",
                task_id=review_task.id,
                status="review",
                actor_type="human",
                actor_ref="smoke",
            )
            changed = await service.request_review_changes(
                profile_id="default",
                task_id=review_task.id,
                actor_type="employee",
                actor_ref="reviewer",
                reason_text="Need source citations.",
            )
            await service.add_task_comment(
                profile_id="default",
                task_id=review_task.id,
                message="Citations requested by reviewer.",
                actor_type="human",
                actor_ref="cli_user:alice",
                comment_type="review_feedback",
            )

            inbox = await service.build_employee_inbox(
                profile_id="default",
                owner_type="employee",
                owner_ref="cto",
                task_limit=10,
                event_limit=10,
            )
            board = await service.build_board(profile_id="default", limit_per_column=10)

            stale_task = await service.create_task(
                profile_id="default",
                title="Recover stale claim",
                description="Recover a stale runtime claim.",
                created_by_type="human",
                created_by_ref="smoke",
                owner_type="employee",
                owner_ref="analyst",
            )
            stale_now = datetime.now(timezone.utc)
            async with session_scope(session_factory) as session:
                repo = TaskFlowRepository(session)
                claimed = await repo.claim_next_runnable_task(
                    now_utc=stale_now,
                    lease_until=stale_now - timedelta(minutes=2),
                    claim_token="smoke-stale-claim",
                    claimed_by="taskflow-runtime:smoke",
                    profile_id="default",
                    owner_ref="analyst",
                )
                assert claimed is not None
                task_run = await repo.create_task_run(
                    task_id=stale_task.id,
                    attempt=claimed.current_attempt,
                    owner_type=claimed.owner_type,
                    owner_ref=claimed.owner_ref,
                    execution_mode="detached",
                    status="running",
                    session_id=f"taskflow:{stale_task.id}",
                    run_id=None,
                    worker_id="taskflow-runtime:smoke",
                    started_at=stale_now - timedelta(minutes=3),
                )
                attached = await repo.attach_task_run(
                    task_id=stale_task.id,
                    claim_token="smoke-stale-claim",
                    task_run_id=task_run.id,
                    session_id=f"taskflow:{stale_task.id}",
                )
                assert attached is True
                started = await repo.mark_task_started(
                    task_id=stale_task.id,
                    claim_token="smoke-stale-claim",
                    started_at=stale_now - timedelta(minutes=3),
                )
                assert started is True

            stale_before = await service.list_stale_task_claims(profile_id="default", limit=10)
            repaired_count = await runtime.sweep_expired_claims(
                worker_id="taskflow-smoke",
                profile_id="default",
                limit=10,
            )
            stale_after = await service.list_stale_task_claims(profile_id="default", limit=10)
            stale_repaired = await service.get_task(profile_id="default", task_id=stale_task.id)
            stale_events = await service.list_task_events(profile_id="default", task_id=stale_task.id)

            return {
                "ok": True,
                "root_dir": str(root_dir),
                "db_path": str(db_path),
                "flow_id": flow.id,
                "checks": {
                    "dependency_unblocked": publish_after.status == "todo",
                    "review_changes_blocked": changed.status == "blocked",
                    "employee_inbox_visible": inbox.total_count >= 1,
                    "board_generated": board.total_count >= 1,
                    "stale_detected": len(stale_before) == 1,
                    "stale_repaired": repaired_count == 1
                    and len(stale_after) == 0
                    and stale_repaired.status == "todo"
                    and any(item.event_type == "lease_expired" for item in stale_events),
                },
            }
        finally:
            await runtime.shutdown()
            await engine.dispose()


def main() -> None:
    payload = asyncio.run(_run_smoke())
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    checks = payload.get("checks", {})
    if not isinstance(checks, dict) or not all(bool(value) for value in checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
