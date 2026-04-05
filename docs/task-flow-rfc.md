# Task Flow RFC

Date: 2026-04-05
Status: Draft
Branch: `codex/task-flow-design-plan`

## 0. Implementation Status Snapshot

Implemented on this branch:

- separate `Task Flow` models: `task_flow`, `task`, `task_dependency`, `task_run`
- separate `Task Flow` service, repository, CLI, and tool namespace
- detached runtime daemon for AI-owned tasks with claims, leases, retries, and isolated `taskflow:<task_id>` sessions
- dependency-driven readiness and downstream unblocking on `completed`
- human startup notices in `afk chat`
- `task.flow.*`, `task.*`, `task.dependency.*`, and `task.run.*` surfaces
- runtime-aware backlog scope so an assigned AI profile can manage the originating task backlog profile
- trusted `transport=taskflow` runtime prompt overlay

Still better treated as next-phase work, not v1 blockers:

- richer operator UX for kanban/review board presentation
- bulk task operations
- separate comment/event stream on top of `task_run`
- richer retry/backoff/operator controls

## 1. Intent

This document defines a new `Task Flow` domain for AFKBOT.

Core decision:

- `Task Flow` is a new subsystem.
- Existing `automation` stays separate.
- Existing `subagent_task` stays separate.
- `Task Flow` may reuse existing runtime primitives, but it must not redefine current automation semantics.

The target model is closer to:

- `Automation` = trigger and schedule
- `Task` = durable work item
- `Task Flow` = dependency graph and assignment model over tasks
- `Subagent` = one execution primitive that can be used by tasks, but is not the task system itself

## 2. Product Decisions

Decisions fixed for this RFC:

- No reuse of `automation` rows as tasks.
- No mutation of `subagent_task` into a generic kanban/task entity.
- `Task Flow` is dependency-driven. A separate bespoke `task_flow engine` is not required at first if dependency orchestration covers sequencing.
- Tasks support an assignee that can be either an AI profile or a human.
- Tasks may be started manually, by tools/API, or by runtime workers in detached mode.
- Current automation behavior must remain unchanged.

## 3. Goals

- Add durable profile-scoped tasks that exist independently from one chat turn.
- Add kanban-like task states and queue visibility.
- Add detached execution for AI-owned tasks without blocking the requester.
- Add dependency-based sequencing so a chain of tasks can represent a flow.
- Add one assignee model that supports both AI and human owners.
- Keep execution/audit visible and queryable.

## 4. Non-Goals

- No rewrite of cron/webhook automation.
- No rewrite of subagent runtime or child session model.
- No full BPMN/DAG engine in v1.
- No major change to chat session semantics.
- No automatic migration from old automation rows into tasks.

## 5. Current AFKBOT State

Relevant existing capabilities:

- Automation CRUD and runtime already exist in `afkbot/cli/commands/automation.py`, `afkbot/services/automations/service.py`, and `afkbot/services/automations/runtime_daemon.py`.
- Webhook and cron execution already use claims, leases, and detached runtime orchestration in `afkbot/services/automations/cron_execution.py`, `afkbot/services/automations/webhook_execution.py`, and `afkbot/repositories/automation_repo_claims.py`.
- Detached persisted subagent runs already exist in `afkbot/models/subagent_task.py`, `afkbot/services/subagents/lifecycle.py`, `afkbot/services/subagents/runtime_support.py`, and `afkbot/workers/subagent_worker.py`.
- Tool exposure and policy gating already exist in `afkbot/services/tools/plugins/__init__.py`, `afkbot/services/agent_loop/tool_exposure.py`, `afkbot/services/agent_loop/tool_invocation_gates.py`, and `afkbot/services/policy/engine.py`.
- API runtime lifecycle already exists in `afkbot/api/app.py` and `afkbot/services/agent_loop/api_runtime.py`.
- Run tracing already exists in `afkbot/repositories/runlog_repo.py` and `afkbot/services/agent_loop/runlog_runtime.py`.

Current gaps:

- No durable `Task` domain.
- No task dependency model.
- No assignee abstraction for AI vs human.
- No task queue separate from automation triggers.
- No task-specific CLI/API/tool surface.
- No task board/list model.
- No task execution ledger separate from one detached subagent execution.

## 6. Proposed Domain Model

V1 introduces five new core entities.

### 6.1 `task_flow`

Represents one lightweight container for related tasks.

Suggested fields:

- `id`
- `profile_id`
- `title`
- `description`
- `status`
- `created_by_type`
- `created_by_ref`
- `default_owner_type`
- `default_owner_ref`
- `labels_json`
- `created_at`
- `updated_at`
- `closed_at`

This is a grouping and reporting primitive, not an execution engine.
Standalone tasks remain valid with `flow_id = null`.

### 6.2 `agent_task`

Represents one durable work item.

Suggested fields:

- `id`
- `profile_id`
- `flow_id` nullable
- `title`
- `description`
- `prompt`
- `status`
- `priority`
- `due_at`
- `created_by_type`
- `created_by_id`
- `assignee_type`
- `assignee_id`
- `source_type`
- `source_id`
- `project_tag`
- `tags_json`
- `blocked_reason`
- `review_notes`
- `created_at`
- `updated_at`
- `started_at`
- `finished_at`
- `claimed_at`
- `lease_until`
- `claim_token`
- `attempt_count`
- `last_error_code`
- `last_error_reason`

### 6.3 `agent_task_dependency`

Represents one directed dependency.

Suggested fields:

- `task_id`
- `depends_on_task_id`
- `dependency_type`

V1 should support one simple dependency type:

- `finish_to_start`

This is enough to express sequential flow and basic kanban gating.

### 6.4 `agent_task_run`

Represents one execution attempt.

Suggested fields:

- `id`
- `task_id`
- `run_kind`
- `status`
- `session_id`
- `run_id`
- `worker_id`
- `started_at`
- `finished_at`
- `output`
- `error_code`
- `reason`

This separates the task from retry history.

### 6.5 `task_owner`

This can stay virtual in v1 and be modeled by `assignee_type` + `assignee_id`.

Supported values:

- `ai_profile`
- `human`

For `ai_profile`, `assignee_id` is a profile id.
For `human`, `assignee_id` is an external identifier or local opaque user key.

## 7. State Model

Recommended task states:

- `todo`
- `claimed`
- `running`
- `blocked`
- `review`
- `completed`
- `failed`
- `cancelled`

State meaning:

- `todo`: ready but not owned by a worker yet
- `claimed`: runtime reserved the task, execution not started
- `running`: active detached execution
- `blocked`: waiting on dependency, human input, approval, credential, or explicit pause
- `review`: execution finished but requires human/agent approval before downstream dependencies open
- `completed`: finished successfully
- `failed`: terminal failed state
- `cancelled`: terminal administrative stop

Important distinction:

- dependency gating should not rely only on `blocked`
- a task with unmet dependencies can be computed as "not ready" even while stored as `todo`

Recommended readiness rule:

- a task is claimable only if all hard dependencies are in `completed`

## 8. Why Dependencies Are Enough for V1 Flow

The user goal is effectively a kanban-plus-dependency model.

That means a dedicated flow engine is not required in V1 if the runtime can do three things:

- determine whether a task is ready
- claim only ready tasks
- update downstream task readiness when an upstream task enters `completed`

This yields:

- linear flows
- fan-in
- fan-out
- mixed human and AI handoffs

Without introducing:

- branching DSL
- flow scripting language
- separate flow scheduler semantics

The lightweight persisted `task_flow` row is still useful in v1 for grouping, labels,
default ownership, and operator navigation. The important boundary is:

- use `task_flow` as a container;
- use `task` + `task_dependency` as the orchestration mechanism;
- do not introduce a separate imperative flow engine in v1.

## 9. Assignment Model

### 9.1 AI-owned tasks

When `assignee_type=ai_profile`:

- the task may be claimed by runtime workers for that profile
- detached execution is allowed
- result may produce chat/session/run traces

### 9.2 Human-owned tasks

When `assignee_type=human`:

- runtime must not auto-execute the task
- it should appear in task listings and boards
- completion/transition requires explicit human action through CLI/API/UI

### 9.3 Unassigned tasks

Optional in V1:

- allow unassigned tasks and let an operator assign later
- or restrict V1 to explicit assignee only

Recommended V1:

- allow unassigned tasks for backlog capture
- only AI-owned and ready tasks are claimable by workers

## 10. Execution Architecture

### 10.1 Runtime split

Task Flow should have its own runtime service.

Do not extend `automations/runtime_daemon.py` directly into mixed semantics.

Recommended split:

- keep existing automation daemon unchanged
- add `task_flow` service/runtime package
- optionally run both under `afk start` as sibling managed services

### 10.2 Reused primitives

These should be reused conceptually, and where possible structurally:

- claim and lease patterns from `afkbot/repositories/automation_repo_claims.py`
- detached worker spawn model from `afkbot/services/subagents/runtime_support.py`
- persisted run tracing from `afkbot/repositories/runlog_repo.py`
- API runtime lifecycle and shared DB setup from `afkbot/services/agent_loop/api_runtime.py`
- policy gating from `afkbot/services/policy/engine.py`
- tool catalog registration from `afkbot/services/tools/plugins/__init__.py`

### 10.3 Detached execution modes

Task execution should support:

- `manual`: created but not auto-run
- `detached`: runtime claims and executes outside terminal
- `human_only`: visible but never auto-run

Detached AI tasks should start one isolated execution session and persist:

- session id
- run id
- final output
- error details

### 10.4 Execution implementation boundary

Task Flow should not require changing current subagent semantics.

Instead:

- Task Flow can create its own worker entrypoint
- that worker may internally use `AgentLoop`
- task execution may optionally call subagents, but the task runtime itself should not be "subagent.run with a different label"

This keeps domains clean.

## 11. Integration Boundaries

### 11.1 Automation

Automation remains separate.

Allowed future integration:

- an automation may create a task

Not allowed:

- task rows stored in automation tables
- task flow readiness coupled to cron/webhook metadata
- task runtime implemented by overloading automation status fields

### 11.2 Subagents

Subagents remain separate.

Allowed future integration:

- a task worker may call a subagent during execution
- a task may record that it used a subagent

Not allowed:

- `subagent_task` becoming the task ledger
- task board reading from `subagent_task`
- task dependencies implemented through parent subagent sessions

### 11.3 Chat

Chat is an interaction surface, not the task source of truth.

Allowed:

- create/update/list tasks from chat tools
- summarize task board in chat

Not allowed:

- task state existing only as a chat-local plan snapshot

## 12. CLI, Tool, and API Surface

### 12.1 CLI

Recommended new command group:

- `afk task`

Suggested commands:

- `afk task list`
- `afk task show`
- `afk task create`
- `afk task update`
- `afk task assign`
- `afk task block`
- `afk task complete`
- `afk task fail`
- `afk task cancel`
- `afk task claim`
- `afk task release`
- `afk task runs`
- `afk task board`

Optional V1.1:

- `afk task dependency add`
- `afk task dependency remove`

### 12.2 Tool plugins

Recommended new tools:

- `task.create`
- `task.list`
- `task.get`
- `task.update`
- `task.assign`
- `task.complete`
- `task.block`
- `task.cancel`

These should be automation-intent-like gated only if needed.

Better model:

- add a new explicit `task intent` gate, separate from automation intent

### 12.3 API

Recommended new router family:

- `/v1/tasks/...`

Suggested endpoints:

- `GET /v1/tasks`
- `POST /v1/tasks`
- `GET /v1/tasks/{id}`
- `POST /v1/tasks/{id}/assign`
- `POST /v1/tasks/{id}/complete`
- `POST /v1/tasks/{id}/block`
- `POST /v1/tasks/{id}/cancel`
- `GET /v1/tasks/{id}/runs`
- `GET /v1/tasks/board`

This should be separate from `/v1/chat` and separate from automation runtime HTTP ingress.

## 13. Suggested Package Layout

Recommended new package family:

- `afkbot/models/agent_task.py`
- `afkbot/models/agent_task_dependency.py`
- `afkbot/models/agent_task_run.py`
- `afkbot/repositories/agent_task_repo.py`
- `afkbot/repositories/agent_task_claims.py`
- `afkbot/services/task_flow/contracts.py`
- `afkbot/services/task_flow/service.py`
- `afkbot/services/task_flow/runtime_daemon.py`
- `afkbot/services/task_flow/runtime_http.py`
- `afkbot/services/task_flow/runtime_target.py`
- `afkbot/services/task_flow/dispatcher.py`
- `afkbot/services/task_flow/worker.py`
- `afkbot/services/task_flow/validators.py`
- `afkbot/services/task_flow/metadata.py`
- `afkbot/services/tools/plugins/task_create/...`
- `afkbot/services/tools/plugins/task_list/...`
- `afkbot/services/tools/plugins/task_get/...`
- `afkbot/services/tools/plugins/task_update/...`
- `afkbot/services/tools/plugins/task_assign/...`
- `afkbot/services/tools/plugins/task_complete/...`
- `afkbot/services/tools/plugins/task_block/...`
- `afkbot/services/tools/plugins/task_cancel/...`
- `afkbot/cli/commands/task.py`
- `afkbot/api/routes_tasks.py`

## 14. Impact Analysis

### 14.1 High-probability files to change

These are expected integration points.

- `afkbot/cli/main.py`
  - register a new top-level `task` command group
- `afkbot/api/app.py`
  - include new task router
- `afkbot/models/__init__.py`
  - register new ORM models
- `afkbot/repositories/__init__.py`
  - export task repositories
- `afkbot/settings.py`
  - add task plugin defaults and task runtime settings
- `afkbot/services/tools/plugins/__init__.py`
  - register new task tool plugins
- `afkbot/cli/commands/start.py`
  - decide whether the task runtime daemon starts with the existing local stack
- `afkbot/db/bootstrap_runtime.py`
  - schema bootstrap for new tables

### 14.2 Medium-probability files to change

These may need additive integration only.

- `afkbot/services/agent_loop/tool_exposure.py`
  - surface new tools
- `afkbot/services/agent_loop/tool_invocation_gates.py`
  - optional new task-intent gate
- `afkbot/services/policy/engine.py`
  - policy checks apply automatically, but docs or presets may need updates
- `afkbot/services/policy/presets_catalog.py`
  - capabilities text may need a new task-flow entry
- `afkbot/api/routes_health.py`
  - optionally include task runtime health in diagnostics

### 14.3 Files best left untouched or only thinly depended on

- `afkbot/services/automations/*`
  - treat as a separate subsystem; only mirror patterns, do not repurpose rows
- `afkbot/models/automation.py`
  - no task semantics added
- `afkbot/models/automation_trigger_cron.py`
  - no task ownership or dependency fields added
- `afkbot/models/automation_trigger_webhook.py`
  - no task execution reuse
- `afkbot/models/subagent_task.py`
  - keep detached subagent lifecycle separate
- `afkbot/services/subagents/*`
  - reuse ideas, avoid semantic overload

## 15. Hidden Risks

### 15.1 Domain confusion

If `automation`, `task`, and `subagent` are not clearly separated in code and UX, operators will not know:

- when to create a webhook
- when to create a task
- when to spawn a subagent

### 15.2 Assignment ambiguity

Human assignees require clear semantics:

- are they local AFKBOT identities
- external channel users
- opaque business users

V1 should avoid identity sprawl and allow a simple opaque `human` owner id.

### 15.3 Dependency deadlocks

Dependency graphs introduce:

- cycles
- unreachable tasks
- permanently blocked chains

Validation must reject cyclic dependencies.

### 15.4 Mixed execution traces

If tasks, automations, and subagents all share the same run/session presentation without labels, observability will degrade.

Task runs need distinct session naming and event tags.

### 15.5 Runtime ownership conflicts

Claim/lease logic must be atomic.

Otherwise:

- two workers may execute the same task
- downstream tasks may open twice
- retries may race with late completions

### 15.6 Scope leaks

An AI profile must not claim tasks assigned to:

- another profile
- a human
- another tenant/profile namespace

## 16. Rollout Plan

### Phase 0: Design and boundaries

- approve domain terminology
- approve state machine
- approve assignment model
- approve dependency semantics

### Phase 1: Persistence and read paths

- add models and repositories
- add validation and metadata mappers
- add CLI and API read paths
- add task create/update/list/get

### Phase 2: Runtime ownership

- add claim/lease logic
- add dispatcher
- add detached worker
- add task run ledger

### Phase 3: Dependency orchestration

- add dependency validation
- compute readiness from completed upstream tasks
- unblock downstream tasks on completion
- add board and flow grouping views

### Phase 4: Human handoff and review

- add `review` flows
- add `human` assignment actions
- add operator controls and audit improvements

## 17. Specification Checklist

Before implementation starts, these must be explicit:

- exact `status` enum
- exact `priority` enum or numeric policy
- whether `review` blocks downstream tasks
- whether unassigned tasks are allowed
- how `human` ids are represented
- whether tasks can reassign themselves
- whether one task can create child tasks dynamically in V1
- whether `blocked` is system-derived, user-set, or both
- whether task ordering inside a board is priority-only or explicit rank-based

## 18. Recommended References

External references:

- OpenClaw Automation and Tasks
- OpenClaw Background Tasks
- OpenClaw Task Flow
- OpenClaw Agent Send
- OpenClaw Sub-Agents

Internal AFKBOT references:

- `afkbot/services/automations/*`
- `afkbot/services/subagents/*`
- `afkbot/services/agent_loop/*`
- `afkbot/repositories/runlog_repo.py`
- `afkbot/cli/commands/start.py`
- `afkbot/api/app.py`

## 19. Final Recommendation

Implement `Task Flow` as a first-class new domain with dependencies-as-flow, additive runtime services, and separate CLI/API/tool surfaces.

Do not:

- retrofit it into automation
- retrofit it into subagent_task
- represent it only as chat plans

Do:

- reuse proven runtime primitives
- keep ownership and dependencies explicit
- keep AI and human assignment in one model
- let automations optionally create tasks later through a clean integration point
