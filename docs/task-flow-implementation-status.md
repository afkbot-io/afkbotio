# Task Flow Implementation Status

Date: 2026-04-05
Branch: `codex/task-flow-design-plan`
Status: release-ready candidate

## Scope

`Task Flow` is implemented as a separate subsystem from both:

- `automation` for cron/webhook triggers
- `subagent_task` for child-agent execution

Current model:

- `task_flow` is a lightweight container
- `task` is the durable work item
- `task_dependency` is the orchestration edge
- `task_run` is the execution attempt ledger
- `task_event` is the append-only task history trail
- detached execution is handled by a dedicated `taskflow` runtime transport

## Implemented

### Domain and persistence

- durable `task_flow`, `task`, `task_dependency`, and `task_run` models
- repository/service layer for task CRUD, flow CRUD, dependency reconciliation, and startup summaries
- board/report read model for kanban-style backlog inspection
- task owner model supporting both `ai_profile` and `human`
- dependency graph with cycle prevention and readiness reconciliation
- execution history via `task_run`
- append-only history via `task_event`
- append-only task discussion/comments via `comment_added` events
- per-channel human notification cursors for startup/inbox dedupe

### Runtime

- dedicated `TaskFlowRuntimeDaemon`
- claim/lease/refresh/finalize flow for AI-owned tasks
- detached background execution under `transport="taskflow"`
- dependency unblocking on upstream completion
- safe handling for lost start transition and lost finalization races
- automatic sweep of expired `claimed` / `running` leases before reclaiming new work
- human handoff persistence from inside a running task without finalization overwrite
- owner reassignment of `claimed` / `running` tasks releases active claim before handoff

### AI/operator surface

- CLI: `afk task ...`
- tools:
  - `task.board`
  - `task.create`, `task.list`, `task.get`, `task.update`
  - `task.event.list`
  - `task.inbox`
  - `task.stale.list`, `task.stale.sweep`
  - `task.review.list`, `task.review.approve`, `task.review.request_changes`
  - `task.flow.create`, `task.flow.list`, `task.flow.get`
  - `task.dependency.add`, `task.dependency.list`, `task.dependency.remove`
  - `task.run.list`, `task.run.get`
- Task Flow skill at `afkbot/skills/taskflow/SKILL.md`
- startup notice for the current human inbox when chat starts, including review items routed via `reviewer_*`
- startup notice now uses an LLM-first digest with deterministic fallback from the same human inbox summary model
- `task.inbox` is read-only for tool execution; seen cursors are advanced only by trusted startup/CLI flows
- explicit stale-claim inspection and repair surfaces now exist in both CLI and tool layers
- release verification checklist is documented in `docs/task-flow-release-checklist.md`

## Verified invariants

- `Task Flow` runtime is started separately from automation runtime
- AI-owned tasks are picked up without an interactive terminal
- worker errors do not kill the daemon loop
- dependency edges can be added and removed after task creation
- cyclic dependency edges are rejected
- human reassignment/review handoff inside a running task is durable
- `task.board` exposes stable derived counts for `todo`, `blocked`, `running`, `review`, `completed`, `failed`, and `cancelled`
- dedicated review inbox and explicit review actions work on top of `reviewer_*` routing
- task execution history is queryable per task and per profile
- task event history is queryable per task and captures create/update/review/dependency/runtime outcome transitions
- human inbox summary is queryable directly and powers startup-style notifications
- human inbox unseen counts are lossless even when relevant events are buried behind newer irrelevant runtime noise
- stale task claims are released back to `todo` and the abandoned `task_run` attempt is marked `cancelled`
- operator stale-claim surfaces can inspect and repair only the selected profile backlog

## Post-Release Enhancements

These are product improvements, not blockers for the current release candidate:

- richer board UI beyond CLI/tools
- bulk task creation/edit flows
- richer review discussion UX on top of `task_event`
- external delivery notifications for human-owned `review` / `blocked` states beyond chat startup

## Recommended post-release tranche

1. Add richer operator review/discussion surfaces on top of `task_event` + comments.
2. Add external delivery notifications for human review backlog and overdue tasks.
3. Add bulk operator flows for multi-task planning and editing.
