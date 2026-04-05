# Task Flow Implementation Status

Date: 2026-04-05
Branch: `codex/task-flow-design-plan`
Status: active implementation

## Scope

`Task Flow` is implemented as a separate subsystem from both:

- `automation` for cron/webhook triggers
- `subagent_task` for child-agent execution

Current model:

- `task_flow` is a lightweight container
- `task` is the durable work item
- `task_dependency` is the orchestration edge
- `task_run` is the execution attempt ledger
- detached execution is handled by a dedicated `taskflow` runtime transport

## Implemented

### Domain and persistence

- durable `task_flow`, `task`, `task_dependency`, and `task_run` models
- repository/service layer for task CRUD, flow CRUD, dependency reconciliation, and startup summaries
- board/report read model for kanban-style backlog inspection
- task owner model supporting both `ai_profile` and `human`
- dependency graph with cycle prevention and readiness reconciliation
- execution history via `task_run`

### Runtime

- dedicated `TaskFlowRuntimeDaemon`
- claim/lease/refresh/finalize flow for AI-owned tasks
- detached background execution under `transport="taskflow"`
- dependency unblocking on upstream completion
- safe handling for lost start transition and lost finalization races
- human handoff persistence from inside a running task without finalization overwrite
- owner reassignment of `claimed` / `running` tasks releases active claim before handoff

### AI/operator surface

- CLI: `afk task ...`
- tools:
  - `task.board`
  - `task.create`, `task.list`, `task.get`, `task.update`
  - `task.flow.create`, `task.flow.list`, `task.flow.get`
  - `task.dependency.add`, `task.dependency.list`, `task.dependency.remove`
  - `task.run.list`, `task.run.get`
- Task Flow skill at `afkbot/skills/taskflow/SKILL.md`
- startup notice for the current human inbox when chat starts, including review items routed via `reviewer_*`

## Verified invariants

- `Task Flow` runtime is started separately from automation runtime
- AI-owned tasks are picked up without an interactive terminal
- worker errors do not kill the daemon loop
- dependency edges can be added and removed after task creation
- cyclic dependency edges are rejected
- human reassignment/review handoff inside a running task is durable
- `task.board` exposes stable derived counts for `todo`, `blocked`, `running`, `review`, `completed`, `failed`, and `cancelled`
- task execution history is queryable per task and per profile

## Remaining optional work

These are product improvements, not blockers for the current v1 core:

- review comments / event trail on top of `task_run`
- separate reviewer inbox surfaces beyond startup notice
- richer board UI beyond CLI/tools
- bulk task creation/edit flows
- notifications for human-owned `review` / `blocked` states
- sweeper / maintenance CLI for stale tasks and operator repair flows

## Recommended next tranche

1. Add append-only task event / review comment model.
2. Add dedicated reviewer inbox CLI/tools on top of `reviewer_*` routing.
3. Add notification hooks for human review backlog and overdue tasks.
