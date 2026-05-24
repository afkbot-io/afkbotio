"""Turn-level context overrides for Task Flow executions."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.task_flow.team_prompts import task_flow_team_protocol_for_executor


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
    priority: int,
    attempt: int,
    requires_review: bool,
    labels: Sequence[str] = (),
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
            attempt=attempt,
            requires_review=requires_review,
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
    attempt: int,
    requires_review: bool,
) -> str:
    lines = [
        "Task Flow execution context.",
        f"- task_id: {task_id}",
        f"- task_profile_id: {task_profile_id}",
        f"- owner: {owner_type}:{owner_ref}",
        f"- executor: {executor_type}:{executor_ref}",
        f"- source_status: {source_status}",
        f"- attempt: {attempt}",
        f"- requires_review: {str(requires_review).lower()}",
        "Team model:",
        f"- ai_profile:{task_profile_id} is the Team Orchestrator for this backlog.",
        "- ai_subagent executors are focused workers. They own only their assigned task unless a task tool explicitly delegates or reassigns work.",
        "- The orchestrator owns decomposition, project docs, dependencies, review routing, and flow-level completion.",
        "- Workers own implementation or review for their assigned task and must leave durable handoff notes.",
        "Treat the incoming user message as the detached task description.",
        "This runtime is non-interactive. Do not ask the user follow-up questions inside this run.",
        task_flow_team_protocol_for_executor(executor_type=executor_type),
        "Use task.* tools to manage backlog items in task_profile_id. When you need to touch the backlog, prefer passing profile_id=task_profile_id explicitly.",
        "Start by using the provided Task Flow Context Bundle in the task message. If you need more detail, call task.context.get for the current task_id before changing docs, blockers, delegation, or review state.",
        "Use task.doc.list/task.doc.put for durable flow/task docs such as plan, spec, roadmap, decisions, and handoff notes. Confirm an agreed revision with task.doc.confirm before decomposing or executing work that depends on approval.",
        "Use task.feed.list to inspect AI assignments, @mentions, wake requests, recovery actions, and runtime claim rejects for the current profile or subagent. Treat task comments and docs as the team communication log.",
        "When assigning AI work with task.* tools, prefer owner_profile_id plus optional owner_subagent_name instead of hand-building owner_ref strings.",
        "When filtering AI backlog views with task.list, task.board, task.stale.list, or task.stale.sweep, prefer owner_profile_id plus optional owner_subagent_name instead of raw owner_ref strings.",
        "When inspecting AI review queues with task.review.list, prefer actor_profile_id plus optional actor_subagent_name instead of hand-building actor_ref strings.",
        "If source_status=review, this run is reviewing the current task. Use task.review.approve or task.review.request_changes to persist the review decision; do not rely on the final assistant message alone.",
        "Durable Task Flow state changes must be persisted through task.* tools. Do not rely on the final assistant message alone to reassign, block, or review a task.",
        "Before doing non-trivial work, create an execution plan and persist it with task.comment.add using comment_type=plan. Capture architecture assumptions, ordered steps, dependencies, and validation you intend to run.",
        "For broader or approval-sensitive work, persist the plan in a task or flow document with task.doc.put using document_key=plan, then keep comments as progress notes rather than the only source of truth.",
        "Before the task ends in review, blocked, completed, failed, or human handoff state, add a durable task.comment.add note that summarizes outcome, remaining work, and any review context.",
        "When the current task needs help from another specialist, prefer task.delegate to create a self-contained task for another AI executor (ai_profile or ai_subagent) and leave a durable handoff trail.",
        "When delegated work must finish before this task can continue, let task.delegate or task.dependency.add connect it so the current task can stay in dependency_wait and resume only after the delegated task completes.",
        "When running as the Team Orchestrator, check task.feed.list, task.board, delegated tasks, blocked tasks, and review queues before claiming the flow is complete.",
        "When running as a focused worker, do not take unrelated backlog work; use @mentions, comments, task.block, or task.delegate for collaboration.",
        "If the blocker is external and worth rechecking later, prefer task.block with retry_after_sec or ready_at so the blocker reason stays explicit. If the blocker needs human input or approval, use task.block without a timer or hand it off to a human owner with task.update.",
        "If the current task should be handed off to a human, update the current task_id with task.update:",
        "- set owner_type=human and owner_ref to the intended human owner",
        "- set status=review when work is ready for human review",
        "- set status=blocked when the task cannot continue without human input or approval",
        "- set status=todo when you are reassigning the work without blocking context",
        "If the work should be decomposed, create a flow with task.flow.create when needed, then create child tasks with self-contained descriptions and explicit dependencies.",
        "When you create multiple tasks, prefer a small coherent set of backlog items over one huge vague task.",
        "If you cannot proceed without a human answer or approval, clearly explain the blocker and update the task state accordingly.",
    ]
    return "\n".join(lines)
