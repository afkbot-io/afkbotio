"""Turn-level context overrides for Task Flow executions."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.services.agent_loop.turn_context import TurnContextOverrides


def build_task_flow_context_overrides(
    *,
    task_id: str,
    task_profile_id: str,
    owner_type: str,
    owner_ref: str,
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
        execution_planning_mode="on",
        prompt_overlay=_build_task_flow_prompt_overlay(
            task_id=task_id,
            task_profile_id=task_profile_id,
            owner_type=owner_type,
            owner_ref=owner_ref,
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
    attempt: int,
    requires_review: bool,
) -> str:
    lines = [
        "Task Flow execution context.",
        f"- task_id: {task_id}",
        f"- task_profile_id: {task_profile_id}",
        f"- owner: {owner_type}:{owner_ref}",
        f"- attempt: {attempt}",
        f"- requires_review: {str(requires_review).lower()}",
        "Treat the incoming user message as the detached task description.",
        "This runtime is non-interactive. Do not ask the user follow-up questions inside this run.",
        "Use task.* tools to manage backlog items in task_profile_id. When you need to touch the backlog, prefer passing profile_id=task_profile_id explicitly.",
        "When assigning AI work with task.* tools, prefer owner_profile_id plus optional owner_subagent_name instead of hand-building owner_ref strings.",
        "Durable Task Flow state changes must be persisted through task.* tools. Do not rely on the final assistant message alone to reassign, block, or review a task.",
        "Before doing non-trivial work, create an execution plan and persist it with task.comment.add using comment_type=plan. Capture architecture assumptions, ordered steps, dependencies, and validation you intend to run.",
        "Before the task ends in review, blocked, completed, failed, or human handoff state, add a durable task.comment.add note that summarizes outcome, remaining work, and any review context.",
        "When the current task needs help from another specialist, prefer task.delegate to create a self-contained task for another AI executor (ai_profile or ai_subagent) and leave a durable handoff trail.",
        "When delegated work must finish before this task can continue, let task.delegate or task.dependency.add connect it so the current task can stay in dependency_wait and resume only after the delegated task completes.",
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
