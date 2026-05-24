---
name: taskflow
description: "Task Flow backlog management via `task.*` tools. Use when the user explicitly wants durable tasks, TODO/backlog management, kanban-like flows, decomposition into multiple tasks, dependencies, ownership changes, or human handoff."
aliases: task-flow, task-manager, backlog, kanban, todo-manager
triggers:
  - taskflow
  - task flow
  - backlog
  - todo
  - kanban
  - tasks
  - durable task
  - create task
  - create tasks
  - create backlog
  - break into tasks
  - декомпозируй в задачи
  - задачи
  - бэклог
  - канбан
tool_names:
  - task.board
  - task.block
  - task.comment.add
  - task.comment.list
  - task.context.get
  - task.dependency.add
  - task.dependency.list
  - task.dependency.remove
  - task.doc.confirm
  - task.doc.list
  - task.doc.put
  - task.event.list
  - task.feed.list
  - task.flow.create
  - task.flow.list
  - task.flow.get
  - task.create
  - task.delegate
  - task.inbox
  - task.list
  - task.get
  - task.review.approve
  - task.review.list
  - task.review.request_changes
  - task.run.list
  - task.run.get
  - task.stale.list
  - task.stale.sweep
  - task.update
preferred_tool_order:
  - task.review.list
  - task.feed.list
  - task.context.get
  - task.doc.list
  - task.board
  - task.doc.put
  - task.doc.confirm
  - task.flow.create
  - task.create
  - task.delegate
  - task.comment.add
  - task.block
  - task.dependency.add
  - task.update
  - task.event.list
  - task.run.list
  - task.stale.list
  - task.stale.sweep
  - task.inbox
  - task.list
  - task.flow.list
  - task.get
  - task.flow.get
execution_mode: executable
---
# taskflow

Manage durable Task Flow backlog items via `task.*` tools.

Task Flow has an AI team model:
- the `ai_profile:<profile_id>` executor is the Team Orchestrator for that profile backlog;
- `ai_subagent:<profile_id>:<name>` executors are focused workers;
- the orchestrator owns project docs, decomposition, dependencies, assignment, review routing, and flow-level completion;
- workers own their assigned tasks and communicate through docs, comments, mentions, review actions, and delegated tasks.

Use this skill for explicit backlog/task requests:
- create/update/list/get tasks
- add/remove/list task dependencies
- inspect task execution history and append-only task history
- append comments / review notes and inspect discussion history
- inspect kanban-like board state and status distribution
- inspect reviewer inbox and explicit review transitions
- inspect human inbox / notification-style backlog summary
- inspect AI profile/subagent feeds, @mentions, wake requests, recovery actions, and runtime claim rejects
- maintain flow/task docs for plans, specs, roadmaps, decisions, and handoffs
- confirm specific document revisions before decomposition or execution that needs approval
- kanban-like work tracking
- decompose work into multiple tasks
- assign tasks to AI profiles or humans
- hand off work from AI to a person
- group work under a Task Flow container

Do not use this skill for cron/webhook triggers. That belongs to `automation`.

## Workflow
1. Decide whether the user wants one task or a grouped flow.
2. For multi-step work, create a flow first with `task.flow.create`.
   - Flow docs are created lazily/defaulted for `brief`, `plan`, `roadmap`, `spec`, and `decisions`.
   - Use `task.doc.put` to turn the user's goal into durable project docs instead of relying only on task descriptions.
3. Create tasks with self-contained prompts:
   - state the job clearly
   - name the expected result
   - encode important constraints directly in the prompt
4. For sequencing, create prerequisite tasks first, then create dependent tasks with `depends_on_task_ids`.
5. Treat the profile AI executor as the default orchestrator. Assign execution work to focused subagents when the backlog has matching workers.
6. For reassignment or handoff, use `task.update`:
   - prefer `owner_profile_id=<profile_id>` to assign an AI profile/orchestrator without hand-building `owner_ref`
   - prefer `owner_profile_id=<profile_id>` plus `owner_subagent_name=<subagent_name>` to assign a specific worker subagent
   - raw `owner_type/owner_ref` still work when you already have canonical refs
   - `owner_type=human` to assign a person
   - `status=review` when ready for human review
   - `status=blocked` when waiting for human input/approval
   - `status=todo` when simply re-queueing or reassigning
7. After tool calls, report concrete ids, owners, statuses, and dependencies from payloads.
8. When executing or resuming a task, inspect `task.context.get` first unless the runtime already supplied a sufficient Task Flow Context Bundle.
   - Use the bundle to understand flow docs, task docs, dependencies, delegated tasks, recent comments, and recent events.
9. When the user wants the current backlog picture, inspect `task.board` before narrating the state by hand.
   - Prefer `owner_profile_id=<profile_id>` plus optional `owner_subagent_name=<subagent_name>` over raw `owner_ref` filters for AI executors.
10. When the user wants review work for a specific person/profile, inspect `task.review.list` instead of approximating it from owner filters.
   - Prefer `actor_profile_id=<profile_id>` plus optional `actor_subagent_name=<subagent_name>` for AI reviewers.
11. When an AI profile or subagent needs to see assignments, @mentions, wake requests, recovery actions, or runtime claim rejects, inspect `task.feed.list`.
12. When a plan/spec/roadmap/decision should survive across agents, use `task.doc.put`; use `task.doc.confirm` when the current revision has been accepted.
13. When investigating what happened during background execution, inspect `task.run.list` and `task.run.get` instead of guessing from the current task state.
14. When you need the durable business history for a task, inspect `task.event.list` to see create/update/review/dependency/runtime outcome events in reverse chronological order.
15. When you are acting as the Team Orchestrator, check board, feed, delegated tasks, blocked tasks, and review queues before declaring flow completion.
16. When you are acting as a worker subagent, stay inside the assigned task unless you create a documented delegation, mention, blocker, or review action.
17. When the user wants the human backlog summary or startup-style digest for one person, inspect `task.inbox`.
18. When the user wants durable discussion or review notes on a task, use `task.comment.add` and `task.comment.list`.
19. Treat `task.inbox` as read-only from tool execution; startup/CLI flows advance seen cursors, not background AI turns.
20. When the user needs operator-style repair visibility for stuck background work, inspect `task.stale.list` first and only then use `task.stale.sweep`.
   - Prefer `owner_profile_id=<profile_id>` plus optional `owner_subagent_name=<subagent_name>` when scoping stale AI work.

## Rules
- Prefer a small set of concrete tasks over one vague umbrella task.
- When creating several tasks, create them one by one so later tasks can reference earlier task ids in `depends_on_task_ids`.
- Prefer one flow per coherent project/thread of work, not one giant global flow.
- Prefer flow/task documents for durable project knowledge. Comments are for progress, questions, and handoff notes; documents are the source of truth for plans/specs/roadmaps/decisions.
- For broad work, write or update `document_key=plan` and confirm the agreed revision before creating dependent execution tasks.
- Use @mentions in comments sparingly to wake or ask another AI executor; mention syntax is `@profile_id` or `@profile_id:subagent_name`.
- If a task is being handed to a human, set both the new owner and a status that matches the reason for handoff.
- If you reassign a `claimed` or `running` task, move it out of active execution with `status=todo`, `status=blocked`, or `status=review` as part of the handoff.
- If you need to reassign, block, or review the current task, persist it with `task.update` before the turn ends.
- Before a background task ends in `review`, `blocked`, `completed`, `failed`, or human handoff, add a durable `task.comment.add` note so the task keeps human-readable context beyond raw events and run logs.
- Prefer `task.review.approve` and `task.review.request_changes` over ad hoc `task.update` when handling a task already in `review`.
- Prefer `task.event.list` for operator-style history; prefer `task.run.*` for execution-attempt diagnostics.
- Prefer `task.inbox` for human notification/inbox questions instead of manually filtering `task.list`.
- Prefer `task.feed.list` for AI profile/subagent assignment, mention, wake, recovery, and runtime-reject feeds instead of manually filtering events.
- Use `task.event.list` when you need raw append-only audit history beyond the current AI executor feed.
- Prefer `task.comment.add` for comments-only collaboration instead of overloading `task.update` with fake status changes.
- If a background task appears stale, inspect `task.run.list`, `task.event.list`, and `task.stale.list` first; use `task.stale.sweep` only when the user explicitly wants operator repair.
- Never use `task.*` to describe work abstractly without actually persisting it when the user asked for real backlog changes.
- Never claim success without a successful `task.*` tool result in the current turn.

## Examples
- One AI task:
  create `task.create` with a self-contained prompt and either `owner_profile_id=<profile_id>` or `owner_profile_id=<profile_id>` plus `owner_subagent_name=<subagent_name>`.
- One subagent task:
  create `task.create` with `owner_profile_id=<profile_id>` and `owner_subagent_name=<subagent_name>` when the backlog should be claimed directly by that subagent instead of the profile.
- One flow with several sequential tasks:
  create `task.flow.create`, then create the first task, then create the second with `depends_on_task_ids=[first_task_id]`, then the third with `depends_on_task_ids=[second_task_id]`.
- Human handoff:
  call `task.update` on the current task with `owner_type=human`, `owner_ref=<person>`, and either `status=review`, `status=blocked`, or `status=todo` depending on why the handoff is happening.
- Add dependency later:
  call `task.dependency.add` when tasks were created separately and should now be linked in sequence.
- Investigate background run:
  call `task.run.list` for the task, then `task.run.get` for the specific run that matters.
- Inspect durable task history:
  call `task.event.list` to see who created, updated, reviewed, or completed the task and what status changed.
- Add task comment:
  call `task.comment.add` with a durable note, review feedback, or handoff context that should remain attached to the task.
- Inspect task discussion:
  call `task.comment.list` to see append-only comments without mixing them with every other task event.
- Inspect current board:
  call `task.board` with optional `owner_profile_id` plus optional `owner_subagent_name`, or with canonical `owner_type/owner_ref`, plus optional `flow_id` or `labels` filters to see counts and preview cards per column.
- Inspect task list for one AI executor:
  call `task.list` with `owner_profile_id=<profile_id>` plus optional `owner_subagent_name=<subagent_name>` instead of hand-building `owner_ref`.
- Inspect task context:
  call `task.context.get` with the task id before changing docs, blockers, delegation, or review state.
- Update durable docs:
  call `task.doc.put` with `scope_type=flow|task`, `scope_id`, `document_key=plan|spec|roadmap|decisions|handoff`, and the complete document body.
- Confirm docs:
  call `task.doc.confirm` with `document_id` and `expected_revision` after the current plan/spec revision is accepted.
- Inspect AI feed:
  call `task.feed.list` with `owner_profile_id=<profile_id>` plus optional `owner_subagent_name=<subagent_name>` to see assignments, @mentions, wake requests, stale-claim recovery actions, and runtime claim rejects.
- Inspect reviewer inbox:
  call `task.review.list` with `actor_profile_id=<profile_id>` plus optional `actor_subagent_name=<subagent_name>` for AI reviewers, or `actor_type/actor_ref` for human reviewers, to see tasks currently waiting in review.
- Inspect human inbox:
  call `task.inbox` with the human owner ref to see a startup-style summary with counts and preview tasks.
- Inspect stale claims:
  call `task.stale.list` with optional `owner_profile_id` plus optional `owner_subagent_name` to see expired in-flight AI claims before deciding whether repair is necessary.
- Repair stale claims:
  call `task.stale.sweep` with optional `owner_profile_id` plus optional `owner_subagent_name` to release expired in-flight claims back to `todo` in the selected profile.
- Approve review:
  call `task.review.approve` on a task in `review` to move it to `completed` and unblock downstream work.
- Request changes:
  call `task.review.request_changes` on a task in `review` to keep it non-terminal, persist review feedback, and optionally hand it back to another owner.
