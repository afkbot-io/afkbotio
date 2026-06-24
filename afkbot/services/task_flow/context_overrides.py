"""Turn-level context overrides for Task Flow executions."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.task_flow.team_prompts import task_flow_team_protocol_for_executor
from afkbot.services.task_flow.work_modes import (
    KNOWLEDGE_MAINTENANCE_WORK_MODE,
    MANAGER_INTAKE_WORK_MODE,
)


def build_task_flow_context_overrides(
    *,
    task_id: str,
    task_profile_id: str,
    owner_type: str,
    owner_ref: str,
    executor_type: str,
    executor_ref: str,
    source_status: str,
    flow_id: str | None,
    source_type: str,
    source_ref: str | None,
    work_mode: str,
    priority: int,
    attempt: int,
    requires_review: bool,
    labels: Sequence[str] = (),
    executor_is_manager: bool = False,
) -> TurnContextOverrides:
    """Build trusted prompt overlay plus runtime metadata for detached task execution."""

    taskflow_payload: dict[str, object] = {
        "task_id": task_id,
        "task_profile_id": task_profile_id,
        "owner_type": owner_type,
        "owner_ref": owner_ref,
        "executor_type": executor_type,
        "executor_ref": executor_ref,
        "source_status": source_status,
        "source_type": source_type,
        "work_mode": work_mode,
        "priority": priority,
        "attempt": attempt,
        "requires_review": requires_review,
    }
    if flow_id is not None:
        taskflow_payload["flow_id"] = flow_id
    if source_ref is not None:
        taskflow_payload["source_ref"] = source_ref
    if labels:
        taskflow_payload["labels"] = tuple(str(item) for item in labels if str(item).strip())
    return TurnContextOverrides(
        runtime_metadata={"taskflow": taskflow_payload},
        trusted_runtime_context={
            "taskflow_detached_runtime": {
                "owner_type": executor_type,
                "owner_ref": executor_ref,
                "work_mode": work_mode,
            }
        },
        execution_planning_mode="on",
        prompt_overlay=_build_task_flow_prompt_overlay(
            task_id=task_id,
            task_profile_id=task_profile_id,
            owner_type=owner_type,
            owner_ref=owner_ref,
            executor_type=executor_type,
            executor_ref=executor_ref,
            source_status=source_status,
            work_mode=work_mode,
            attempt=attempt,
            requires_review=requires_review,
            executor_is_manager=executor_is_manager,
        ),
    )


def _build_task_flow_prompt_overlay(
    *,
    task_id: str,
    task_profile_id: str,
    owner_type: str,
    owner_ref: str,
    executor_type: str,
    executor_ref: str,
    source_status: str,
    work_mode: str,
    attempt: int,
    requires_review: bool,
    executor_is_manager: bool = False,
) -> str:
    lines = [
        "Task Flow execution context.",
        f"- task_id: {task_id}",
        f"- task_profile_id: {task_profile_id}",
        f"- owner: {owner_type}:{owner_ref}",
        f"- executor: {executor_type}:{executor_ref}",
        f"- source_status: {source_status}",
        f"- work_mode: {work_mode}",
        f"- attempt: {attempt}",
        f"- requires_review: {str(requires_review).lower()}",
        "Team model:",
        f"- profile:{task_profile_id} is the organization/team boundary for this project.",
        "- Employees are the only Task Flow owners, reviewers, and runtime executors.",
        "- Subagents are helper tools only: they cannot own, claim, review, comment, or complete Task Flow work except through the employee runtime that invoked them.",
        "- Human/operator intake should enter through the profile root employee (usually cto); managers classify the work, delegate only the minimum specialist work needed, route reviews, and maintain project documents through task.* tools.",
        "- Focused employees own implementation or review for their assigned task and leave durable handoff notes.",
        "Treat the incoming user message as the detached task description.",
        "This runtime is non-interactive. Do not ask the user follow-up questions inside this run.",
        _work_mode_protocol(work_mode=work_mode),
        task_flow_team_protocol_for_executor(
            executor_type=executor_type,
            executor_is_manager=executor_is_manager,
        ),
        "Use task.* tools to manage backlog items in task_profile_id. When you need to touch the backlog, prefer passing profile_id=task_profile_id explicitly.",
        "Start by using the Project Knowledge Packet inside the provided Task Flow Context Bundle. If you need more detail, call task.context.get for the current task_id before changing docs, blockers, delegation, or review state.",
        "Use task.doc.list/task.doc.put for the Project Knowledge Spine: flow docs brief, plan, spec, decisions, and status; task docs handoff, notes, review, and evidence. Confirm an agreed revision with task.doc.confirm before decomposing or executing work that depends on approval.",
        "When you generate or receive small task-specific artifacts, attach them with task.create/task.update attachments so future employee runs receive the file content. Use task docs for durable project memory; do not bury canonical decisions only in attached files.",
        "Use task.feed.list to inspect employee assignments, @mentions, wake requests, recovery actions, and runtime claim rejects for this profile. Treat task comments and docs as the team communication log.",
        "When assigning work with task.* tools, use owner_type=employee and owner_ref=<employee_id>.",
        "When filtering backlog views with task.list, task.board, task.stale.list, or task.stale.sweep, filter by owner_type=employee and owner_ref=<employee_id> when needed.",
        "When inspecting review queues with task.review.list, use the employee actor identity supplied by this runtime.",
        "When acting as a manager, also inspect task.review.list with all_reviewers=true before concluding there is no pending review work in the flow.",
        "If source_status=review, focused reviewer runs should use task.review.approve or task.review.request_changes to persist the review decision. Manager-intake runs must delegate or route review decisions instead of approving them directly.",
        "Durable Task Flow state changes must be persisted through task.* tools. Do not rely on the final assistant message alone to reassign, block, or review a task.",
        "When moving a task to review or completed, do not include retry_after_sec or ready_at unless you are intentionally scheduling a blocked revisit.",
        "For non-trivial work, keep the execution plan in the current run summary or in task docs when it must become durable. Use task.comment.add only for meaningful decision, blocker, handoff, review, or final notes.",
        "For broader or approval-sensitive work, persist the plan in a flow document with task.doc.put using document_key=plan, update document_key=status as the project state changes, and avoid duplicating routine progress in comments.",
        "Before the task ends in review, blocked, completed, failed, employee handoff, or operator-blocked state, add a durable task.comment.add note only when it changes team state or gives the next owner/reviewer useful evidence.",
        "Before claiming deployed services are running, re-check the current runtime state at the end of the task and report stopped or dirty states explicitly.",
        "When the current task needs help from another specialist, prefer one focused task.delegate to the single best employee and leave a durable handoff trail. Do not create duplicate sibling tasks for the same owner and goal.",
        "When delegated work must finish before this task can continue, let task.delegate or task.dependency.add connect it so the current task can stay in dependency_wait and resume only after the delegated task completes.",
        "When running as a manager, check task.feed.list, task.board, delegated tasks, blocked tasks, and review queues before claiming the flow is complete.",
        "When running as a focused worker, do not take unrelated backlog work; use @mentions, comments, task.block, or task.delegate for collaboration.",
        "If the blocker is external and worth rechecking later, prefer task.block with retry_after_sec or ready_at so the blocker reason stays explicit. If the blocker needs operator input or approval, keep the task assigned to the responsible employee and use task.block without a timer with a precise question and review context.",
        "Do not hand Task Flow work to a human owner. Humans are audit actors in comments/docs/settings; task owner, reviewer, and default owner principals are employees.",
        "Use status=review when work is ready for another employee to review, status=blocked when the task cannot continue without input or approval, and status=todo when reassigning employee-owned work without blocker context.",
        "If the work should be decomposed, create a flow with task.flow.create when needed, then create the smallest coherent set of child tasks with self-contained descriptions and explicit dependencies.",
        "When you create multiple tasks, keep fanout small. Continue or reassign existing open child work before creating another sibling with the same goal.",
        "When generating project scaffolds, create checked-in env templates (*.env.example) instead of production-looking .env files with placeholder secrets. Do not create or deploy prod env files with fake secrets; require operator-provided secrets or mark generated private env files as local/dev-only and report the risk.",
        "If you cannot proceed without a human answer or approval, clearly explain the blocker and update the task state accordingly.",
    ]
    return "\n".join(lines)


def _work_mode_protocol(*, work_mode: str) -> str:
    if work_mode == KNOWLEDGE_MAINTENANCE_WORK_MODE:
        return "\n".join(
            [
                "Knowledge maintenance work mode.",
                "- Your primary job is to keep flow knowledge accurate and actionable.",
                "- Read the Knowledge Packet, current flow docs, board, feed, blocked tasks, and review queue before changing state.",
                "- Update canonical flow docs instead of scattering project memory across comments.",
                "- Do not implement specialist work in this task; delegate focused employee-owned work when execution is needed.",
                "- Confirm only document revisions you can validate from the available evidence.",
                "- If operator approval is required, block the responsible employee task with reason_code=human_review_required and one precise question.",
            ]
        )
    if work_mode == MANAGER_INTAKE_WORK_MODE:
        return "\n".join(
            [
                "Manager intake work mode.",
                "- Your job is orchestration, not specialist execution.",
                "- Read the Knowledge Packet, board, feed, reviews, docs, dependencies, and source task details before routing work.",
                "- First classify the intake as simple, focused, project, or blocked. Use the smallest sufficient route.",
                "- Convert broad/project intake into focused employee-owned tasks with task.delegate.",
                "- For simple/focused intake, prefer one delegated specialist task and at most one review task unless new evidence proves a wider split is necessary.",
                "- Each delegated task must include the expected output, evidence required, review owner when needed, and how it unblocks the parent task.",
                "- Prefer task.delegate with wait_for_delegated_task=true so the intake task waits on real dependencies.",
                "- Do not create duplicate sibling tasks for the same owner and goal; inspect existing delegated work first.",
                "- Do not run implementation, shell, browser, GitLab/GitHub, or code-review work yourself in this mode.",
                "- Close or move this intake task to review only after delegated work is finished and summarized, or block it with a precise reason.",
            ]
        )
    return "Execution work mode."
