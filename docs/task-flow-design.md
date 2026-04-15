# Task Flow Design Plan

Status: draft
Date: 2026-04-05
Branch: `codex/task-flow-design-plan`

## 0. Current Build Status

Implemented in the current branch:

- Task Flow is a separate subsystem from `automation` and `subagent_task`.
- `Task`, `TaskFlow`, `TaskDependency`, and `TaskRun` are persisted independently.
- AI-owned tasks are executed by a dedicated polling daemon, not by automation cron/webhook runtime.
- Human-owned tasks remain durable backlog items and show up as startup notices in chat.
- Dependency edges can be created, listed, removed, and used to unblock downstream tasks.
- Execution attempts are queryable through `task.run.*` and CLI `run-*` commands.
- Runtime prompt overlays explicitly instruct background agents how to decompose work, update task state, and hand tasks off to humans.

Remaining next-phase improvements:

- board/review UX beyond CLI/tools
- comment/event trail beyond `task_run`
- manual operator dispatch/tick convenience commands
- richer dependency policies and retry controls

## 1. Problem Statement

AFKBOT already has two separate background-capable systems:

- `automation`: scheduled or webhook-triggered work executed by the automation runtime.
- `subagent_task`: detached child-agent execution spawned from an active run.

This proposal adds a third, separate system: `Task Flow`.

`Task Flow` is not a scheduler and is not a replacement for `subagent_task`.
It is a durable work-management domain built around tasks, ownership, dependencies,
claims, review, and background execution for AI-owned tasks.

The user requirements are:

- keep `automation` separate and backwards-compatible;
- keep `subagent_task` separate and unchanged in meaning;
- support kanban-like task states: `todo`, `blocked`, `running`, `review`, `completed`;
- support dependencies so a task graph can behave like a flow without a dedicated flow engine;
- support responsible owner being either an AI profile or a human;
- support detached execution of standalone tasks, similar to OpenClaw background runs;
- make this a first-class product surface called `Task Flow`.

## 2. External Reference Model

OpenClaw keeps these concepts separate:

- Scheduled Tasks / webhooks: trigger work.
- Background Tasks: track detached execution.
- Agent Send / Sub-Agents: spawn separate background agent runs.
- Task Flow: orchestrate work as a durable flow.

Relevant references:

- <https://docs.openclaw.ai/automation>
- <https://docs.openclaw.ai/automation/tasks>
- <https://docs.openclaw.ai/automation/taskflow>
- <https://docs.openclaw.ai/tools/agent-send>
- <https://docs.openclaw.ai/tools/subagents>

The AFKBOT equivalent should preserve the same conceptual split:

- `automation` remains trigger-oriented.
- `subagent_task` remains detached child-run oriented.
- `task flow` becomes work-item and dependency oriented.

## 3. Current AFKBOT Baseline

### Already present

- Automation runtime with queue workers, cron ticking, webhook ingress, claims, leases, and isolated automation sessions.
- Detached `subagent_task` lifecycle with persisted status, worker spawn, wait/result, and child session tracing.
- Existing `Run`, `RunlogEvent`, and `ChatSession` persistence, which can be reused for task execution traceability.
- Policy capability split for `automation` and `subagents`.
- Tool/plugin registry and CLI command-group patterns for new first-class domains.

### Not present

- durable task backlog per profile;
- task owner model;
- task dependency graph;
- review queue;
- background task dispatcher for AI-owned work items;
- separate policy capability and tool namespace for `task flow`;
- standalone human-assigned work-item surface;
- grouped flow container for task collections.

### Expected integration points

Likely affected areas for implementation:

- `afkbot/models/__init__.py`
- `afkbot/db/bootstrap_runtime.py`
- `afkbot/settings.py`
- `afkbot/cli/main.py`
- `afkbot/cli/commands/start.py`
- `afkbot/services/tools/plugins/__init__.py`
- `afkbot/services/policy/presets_contracts.py`
- `afkbot/services/policy/presets_catalog.py`
- `afkbot/cli/presentation/setup_policy_prompts.py`
- `afkbot/services/setup/defaults.py`
- `afkbot/services/agent_loop/turn_preparation_support.py`
- `afkbot/services/agent_loop/loop.py`
- `afkbot/services/agent_loop/safety_policy.py`

New areas to add rather than retrofit heavily:

- `afkbot/models/task_flow.py`
- `afkbot/models/task.py`
- `afkbot/models/task_dependency.py`
- `afkbot/models/task_run.py`
- `afkbot/repositories/task_*.py`
- `afkbot/services/task_flow/*`
- `afkbot/cli/commands/task.py`
- `afkbot/services/tools/plugins/task_*`
- `afkbot/skills/task-flow/SKILL.md`

Areas that should remain semantically unchanged:

- `afkbot/services/automations/*`
- `afkbot/cli/commands/automation.py`
- `afkbot/models/automation.py`
- `afkbot/models/automation_trigger_cron.py`
- `afkbot/models/automation_trigger_webhook.py`
- `afkbot/services/subagents/*`
- `afkbot/cli/commands/subagent.py`
- `afkbot/models/subagent_task.py`

## 4. Goals

- Add a new product surface named `Task Flow`.
- Make tasks durable, queryable, assignable, and dependency-aware.
- Support background execution for AI-owned tasks without using `automation`.
- Support human-owned tasks without forcing runtime execution.
- Allow task graphs to represent flows so no separate flow engine is required in v1.
- Reuse existing runtime mechanics where safe: claims, leases, runs, runlogs, session isolation, policy gates.

## 5. Non-Goals

- No semantic rewrite of `automation`.
- No semantic rewrite of `subagent_task`.
- No attempt to model organizations, teams, or full HR-like identity systems in v1.
- No branching/DAG execution engine beyond dependency-unblocking rules in v1.
- No UI-heavy kanban board implementation in v1; CLI/API/tool surfaces are enough.

## 6. Core Design Principles

1. Separate trigger, work item, and execution attempt.
2. Separate ownership from claim.
3. Separate human tasks from AI-executable tasks.
4. Use dependencies to unlock tasks, not to embed imperative workflows in prompts.
5. Prefer additive runtime/service boundaries over modifying automation semantics.
6. Use one stable product term: `Task Flow`, but keep the domain primitive name `Task`.

## 7. Domain Boundaries

### Task

One durable work item.

Properties:

- what must be done;
- who is responsible;
- whether it is ready, blocked, running, under review, or done;
- what it depends on;
- what execution traces exist.

`Task` is the primary domain primitive.

### Task Flow

A lightweight container for related tasks.

Purpose:

- grouping;
- shared labels/metadata;
- operator view;
- aggregate status;
- reporting and navigation.

Important: `Task Flow` is not an execution engine in v1. The graph of tasks and dependencies
is the orchestration mechanism. The `task_flow` record is a container, not a scheduler.

Standalone tasks are allowed with `flow_id = null`.

### Task Dependency

An edge between tasks.

Each dependency says:

- task `B` cannot become ready until task `A` reaches the required terminal condition;
- usually `completed`;
- later extension may allow `completed_or_skipped` or review gates.

Dependencies drive orchestration.

### Task Owner

The responsible party for the task.

Owner is not the same thing as the worker process and not the same thing as the creator.

Owner types in v1:

- `ai_profile`
- `human`

### Task Claim

An execution reservation held by the runtime for an AI-owned task.

Claim is runtime state:

- claim token;
- claimed by;
- lease expiry;
- only valid for AI-owned, ready tasks.

This should be modeled as part of task execution state on the `task` row, not as the business owner.

### Task Run

One execution attempt for one task.

`TaskRun` is separate from `Task` because a task may be retried or rerun without losing its durable identity.

It links the task to:

- execution attempt number;
- session id;
- run id;
- worker/runtime metadata;
- timestamps;
- exit status;
- summary/error.

### Review

Review is a post-execution gate, not a failure state.

A task enters `review` when:

- it completed execution but requires approval;
- or a policy/rule says the result must be checked before becoming `completed`.

Review must be represented as a distinct state because it is operationally different from both
`running` and `completed`.

### Blocked

Blocked is a first-class state, not an annotation.

Blocked reasons in v1:

- unmet dependency;
- waiting on human input;
- waiting on credential / approval / external condition;
- explicitly blocked by operator.

Blocked tasks are not claimable.

## 8. Proposed Domain Model

### `task_flow`

Recommended fields:

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

Derived status can be computed from member tasks, but storing a cached aggregate status is acceptable.

### `task`

Recommended fields:

- `id`
- `profile_id`
- `flow_id` nullable
- `title`
- `prompt`
- `status`
- `priority`
- `due_at`
- `ready_at`
- `owner_type`
- `owner_ref`
- `reviewer_type` nullable
- `reviewer_ref` nullable
- `source_type`
- `source_ref` nullable
- `created_by_type`
- `created_by_ref`
- `labels_json`
- `requires_review`
- `blocked_reason_code` nullable
- `blocked_reason_text` nullable
- `claim_token` nullable
- `claimed_by` nullable
- `lease_until` nullable
- `current_attempt` default `0`
- `last_session_id` nullable
- `last_run_id` nullable
- `last_error_code` nullable
- `last_error_text` nullable
- `started_at` nullable
- `finished_at` nullable
- `created_at`
- `updated_at`

### `task_dependency`

Recommended fields:

- `task_id`
- `depends_on_task_id`
- `satisfied_on_status` default `completed`
- `created_at`

Unique constraint:

- one directed edge only once.

Hard validation:

- no self-dependency;
- no cross-profile dependency.

### `task_run`

Recommended fields:

- `id`
- `task_id`
- `attempt`
- `owner_type`
- `owner_ref`
- `execution_mode`
- `status`
- `session_id`
- `run_id` nullable
- `worker_id` nullable
- `summary` nullable
- `error_code` nullable
- `error_text` nullable
- `started_at`
- `finished_at` nullable

## 9. Status Model

Canonical task states in v1:

- `todo`
- `claimed`
- `running`
- `blocked`
- `review`
- `completed`
- `failed`
- `cancelled`

Rules:

- `todo` means ready and claimable.
- `blocked` means not claimable.
- `claimed` means reserved by runtime but work not fully started.
- `running` means execution has started.
- `review` means execution finished but final approval is pending.
- `completed`, `failed`, `cancelled` are terminal.

State transitions:

- `blocked -> todo` when all blockers are cleared.
- `todo -> claimed` on successful runtime claim.
- `claimed -> running` when execution starts.
- `running -> completed` on success and no review needed.
- `running -> review` on success with review gate.
- `running -> failed` on runtime or task failure.
- `claimed -> todo` on lease expiry or worker loss.
- `todo|blocked|claimed|running|review -> cancelled` by operator.
- `review -> completed` on approval.
- `review -> blocked` if reviewer requests changes and follow-up work is needed.

## 10. Dependency Model Without a Separate Flow Engine

The key design choice is:

- do not implement a separate imperative `taskflow engine` in v1;
- implement `Task Flow` as a task graph plus dependency-unblocking rules.

How it works:

1. Tasks may belong to the same `task_flow`.
2. A task with unsatisfied dependencies is `blocked` with `blocked_reason_code=dependency_wait`.
3. When a dependency task reaches the required terminal status, the system re-evaluates dependents.
4. If all hard dependencies are satisfied and there are no other blockers, dependent task transitions to `todo`.
5. The flow status is derived from task graph state.

This gives flow behavior without a separate executor:

- sequential flow is just a chain of dependencies;
- fan-in/fan-out is just multiple edges;
- standalone tasks still work.

This also keeps the runtime simple:

- dispatcher only needs to scan for `todo` AI-owned tasks;
- it does not need to understand flow logic beyond dependency readiness.

## 11. Responsible Owner Model

### Why owner must be polymorphic

AFKBOT has a first-class `Profile` entity for AI owners, but no first-class human principal model suitable for durable assignment.

Therefore v1 should use:

- `owner_type`
- `owner_ref`

Examples:

- `owner_type=ai_profile`, `owner_ref=default`
- `owner_type=human`, `owner_ref=telegram:user:123456`
- `owner_type=human`, `owner_ref=connect:client:abc123`

This avoids blocking v1 on a new human identity subsystem.

### Ownership rules

- Only `ai_profile` tasks are auto-claimable by runtime workers.
- `human` tasks are visible and mutable, but never auto-executed by the task dispatcher.
- Reassignment changes ownership, not historical run records.
- Claiming a task does not change the responsible owner.

### Reviewer model

Reviewer should use the same shape:

- `reviewer_type`
- `reviewer_ref`

This makes human review and AI review equally representable.

## 12. Execution Model

### Detached standalone tasks

OpenClaw-like detached background execution should be modeled as:

- create a standalone task with `flow_id = null`;
- assign it to an AI owner;
- set status to `todo`;
- dispatcher claims and runs it in the background.

This avoids creating a second background runtime path.

### AI-owned task execution

Dispatcher behavior:

1. scan claimable tasks;
2. claim one task with lease token;
3. create `task_run`;
4. start isolated agent run in a dedicated session;
5. refresh lease while active;
6. finalize task and task_run;
7. evaluate dependent tasks.

### Human-owned tasks

Human-owned tasks do not invoke runtime execution.

They are progressed via:

- explicit CLI or API updates;
- future channel or connect surfaces;
- review or manual completion commands.

## 13. Runtime, Session, and Metadata Boundaries

### Recommended runtime transport

Use a new trusted ingress transport:

- `transport = "taskflow"`

Do not reuse `transport = "automation"`.

### Recommended runtime metadata

Trusted runtime metadata should look like:

```json
{
  "transport": "taskflow",
  "account_id": "<task-id>",
  "taskflow": {
    "task_id": "<task-id>",
    "flow_id": "<flow-id-or-null>",
    "owner_type": "ai_profile",
    "owner_ref": "<profile-id-or-human-ref>",
    "source_type": "manual|api|automation|task",
    "priority": 50,
    "attempt": 1,
    "labels": ["project-x", "release-1"]
  }
}
```

### Prompt overlay

Each task execution should get a trusted prompt overlay similar to automation runtime:

- this is a Task Flow execution;
- the incoming message is the self-contained task prompt;
- the task has durable identity and status outside the chat;
- the agent should produce a result appropriate for durable completion or review.

### Session naming

Recommended stable session id:

- `taskflow:<task-id>`

Rationale:

- one durable session per task;
- retries create new `Run` rows inside the same session;
- resume/debug story stays simple.

Do not encode flow graph structure into session ids.

### Execution boundaries

- one task execution equals one isolated session;
- a task may call `subagent.run`, but that is subordinate execution inside the task's session;
- `subagent_task` remains an implementation tool, not the Task Flow business model.

## 14. Mandatory Invariants

### Domain invariants

- A task belongs to exactly one profile.
- Dependencies cannot cross profiles.
- A task cannot depend on itself.
- A task cannot be both `todo` and dependency-blocked.
- Only AI-owned tasks may be claimed by the runtime.
- Only one active claim may exist per task.
- A terminal task cannot be claimed.
- A task in `review` is not yet `completed`.

### Runtime invariants

- Claim and lease state must be additive and separate from business ownership.
- Lease expiry must return a non-terminal task to a claimable or blocked state safely.
- Task execution must not reuse automation claim tables or automation session naming.
- Task runtime health must not change automation runtime semantics.

### Policy invariants

- `task.*` tools must not be hidden behind `automation` capability.
- Task Flow needs its own capability id and its own tool-prefix mapping.
- Read-only task tools and mutating task tools should remain distinguishable for strict approval presets.

### Compatibility invariants

- Existing `automation.*` CLI/tools/runtime remain unchanged by default.
- Existing `subagent.*` CLI/tools/runtime remain unchanged by default.
- No automation row should be reinterpreted as a task.
- No subagent task row should be reinterpreted as a Task Flow task.

## 15. Recommended Product Surface

### CLI

Preferred command family:

- `afk task list`
- `afk task show`
- `afk task create`
- `afk task update`
- `afk task complete`
- `afk task fail`
- `afk task block`
- `afk task cancel`
- `afk task claim`

Optional later:

- `afk task-flow list`
- `afk task-flow show`

Recommendation: keep the core work-item surface under `task`, while the overall feature name remains `Task Flow`.

### Operator UX contract

The status vocabulary should be stable across CLI, tools, API, and future UI.

Recommended primary columns:

- `Todo`
- `Blocked`
- `Running`
- `Review`
- `Completed`
- `Failed`
- `Cancelled`

Recommended first-order filters:

- by `owner`
- by `flow`
- by `label`
- by `due_at`
- by `priority`

Recommended summary counters:

- `ready`
- `blocked`
- `running`
- `needs_review`
- `overdue`

This gives the system a kanban shape without requiring a dedicated board implementation in v1.

### Tool namespace

Recommended initial tool namespace:

- `task.list`
- `task.get`
- `task.create`
- `task.update`
- `task.complete`
- `task.fail`
- `task.block`
- `task.cancel`

Later:

- `taskflow.list`
- `taskflow.get`

## 16. Review Semantics

Review should not be overloaded with comments-only metadata.

Minimum contract:

- task can require review by flag or policy;
- review has responsible reviewer;
- approval moves task to `completed`;
- rejection keeps task non-terminal, usually `blocked`;
- review comments should be recorded on task or task_run.

This keeps review operationally meaningful.

## 17. Anti-Patterns to Avoid

1. Do not extend `automation` to hold ad hoc tasks.
2. Do not rename `subagent_task` and pretend it is the task domain.
3. Do not store dependencies as opaque JSON only.
4. Do not model human and AI owner with one foreign key.
5. Do not make `review` a boolean on top of `completed`.
6. Do not let the dispatcher claim tasks whose dependencies are unsatisfied.
7. Do not couple task execution to the current chat REPL plan snapshot.
8. Do not require a polling loop from the main agent to know when a task finished.
9. Do not use tags as a substitute for dependencies.
10. Do not put Task Flow tools behind automation intent detection.

## 18. Suggested Runtime Decomposition

Keep the same process, but separate the service boundary:

- `AutomationsService` and automation daemon remain as-is.
- Add a dedicated `TaskFlowService`.
- Add a dedicated task dispatcher / worker manager.
- Let `afk start` start both subsystems side-by-side.

This provides operational separation without multiplying processes unnecessarily.

## 19. Rollout Shape

Phase 1:

- domain tables;
- CLI and tool read/write surfaces;
- AI-owned standalone task execution;
- dependency-aware readiness;
- human ownership as non-executable task type.

Phase 2:

- lightweight `task_flow` container;
- review queue;
- richer owner references and routing;
- operator dashboards / board views;
- API endpoints.

Phase 3:

- notifications;
- bulk operations;
- automation-to-task handoff as an explicit user-chosen pattern;
- richer dependency policies.

## 20. Required Specifications

Implementation should not start without these written contracts:

- Domain RFC: terminology, goals, non-goals, invariants.
- State machine spec: every allowed task state transition.
- Ownership spec: `owner_type` and `owner_ref` formats and constraints.
- Dependency spec: satisfaction rules, unblock behavior, cycle prevention.
- Runtime metadata spec: trusted `taskflow` transport payload contract.
- Session/run tracing spec: how `task`, `task_run`, `session_id`, and `run_id` relate.
- Tool/API spec: payloads for `task.create`, `task.list`, `task.get`, `task.update`, and terminal actions.
- Policy spec: new capability id, read-only vs mutating tool split, approval behavior.
- Rollout spec: feature-flag or additive launch sequence and compatibility guarantees.

## 21. Implementation Work Breakdown

Epic 1: Domain and persistence

- add `task_flow`, `task`, `task_dependency`, `task_run` models;
- register models and schema bootstrap;
- add repositories and query helpers for ready/blocked/terminal tasks.

Epic 2: Runtime orchestration

- add `TaskFlowService`;
- add dispatcher and worker manager;
- implement claim, lease refresh, finalize, and dependency re-evaluation;
- wire it into `afk start` without changing automation semantics.

Epic 3: Surface area

- add CLI command group;
- add `task.*` tool plugins;
- add skill routing and intent gating;
- add policy capability and approval semantics.

Epic 4: Ownership and review

- implement `owner_type/owner_ref` and reviewer fields;
- support non-executable human tasks;
- implement `review` transitions and reviewer assignment.

Epic 5: Documentation and operator guidance

- publish user-facing docs for Task Flow;
- add skill guidance for `task.*`;
- add compatibility notes explaining how Task Flow differs from automation and subagents.

## 22. Testing Focus

Must-have test areas:

- claim exclusivity;
- lease expiry and recovery;
- dependency unblock transitions;
- cross-profile isolation;
- human-owned tasks never auto-claim;
- AI-owned tasks execute in isolated task sessions;
- review transitions;
- compatibility with automation runtime and subagent runtime;
- strict/medium policy behavior for new task tools.

## 23. Open Questions

- Should `task_flow` exist in v1 as a stored container, or can v1 ship with standalone tasks plus optional grouping by labels?
- Should review comments live directly on `task`, on `task_run`, or in a separate comment/event table?
- Which human identity strings should be canonical in v1: connect user, channel route, generic external principal, or all of the above?
- Should a failed dependency propagate failure to downstream tasks automatically, or leave them blocked until operator decision?
- Should retry create a new task or a new `task_run` attempt on the same task?

## 24. Recommended Decision Summary

- Build `Task Flow` as a new domain, not as an extension of `automation` or `subagent_task`.
- Use tasks plus dependencies as the orchestration model so no dedicated flow engine is required in v1.
- Introduce a lightweight `task_flow` container for grouping and product naming, but keep execution logic task-centric.
- Model responsible owner with `owner_type + owner_ref`.
- Auto-execute only AI-owned tasks.
- Use a dedicated runtime transport and metadata contract for task execution.
- Keep automation and subagents as separate existing subsystems.
