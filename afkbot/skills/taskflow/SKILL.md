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
  - task.dependency.add
  - task.dependency.list
  - task.dependency.remove
  - task.flow.create
  - task.flow.list
  - task.flow.get
  - task.create
  - task.list
  - task.get
  - task.run.list
  - task.run.get
  - task.update
preferred_tool_order:
  - task.board
  - task.flow.create
  - task.create
  - task.dependency.add
  - task.update
  - task.run.list
  - task.list
  - task.flow.list
  - task.get
  - task.flow.get
execution_mode: executable
---
# taskflow

Manage durable Task Flow backlog items via `task.*` tools.

Use this skill for explicit backlog/task requests:
- create/update/list/get tasks
- add/remove/list task dependencies
- inspect task execution history
- inspect kanban-like board state and status distribution
- kanban-like work tracking
- decompose work into multiple tasks
- assign tasks to AI profiles or humans
- hand off work from AI to a person
- group work under a Task Flow container

Do not use this skill for cron/webhook triggers. That belongs to `automation`.

## Workflow
1. Decide whether the user wants one task or a grouped flow.
2. For multi-step work, create a flow first with `task.flow.create`.
3. Create tasks with self-contained prompts:
   - state the job clearly
   - name the expected result
   - encode important constraints directly in the prompt
4. For sequencing, create prerequisite tasks first, then create dependent tasks with `depends_on_task_ids`.
5. For reassignment or handoff, use `task.update`:
   - `owner_type=ai_profile` to assign an AI profile
   - `owner_type=human` to assign a person
   - `status=review` when ready for human review
   - `status=blocked` when waiting for human input/approval
   - `status=todo` when simply re-queueing or reassigning
6. After tool calls, report concrete ids, owners, statuses, and dependencies from payloads.
7. When the user wants the current backlog picture, inspect `task.board` before narrating the state by hand.
8. When investigating what happened during background execution, inspect `task.run.list` and `task.run.get` instead of guessing from the current task state.

## Rules
- Prefer a small set of concrete tasks over one vague umbrella task.
- When creating several tasks, create them one by one so later tasks can reference earlier task ids in `depends_on_task_ids`.
- Prefer one flow per coherent project/thread of work, not one giant global flow.
- If a task is being handed to a human, set both the new owner and a status that matches the reason for handoff.
- If you reassign a `claimed` or `running` task, move it out of active execution with `status=todo`, `status=blocked`, or `status=review` as part of the handoff.
- If you need to reassign, block, or review the current task, persist it with `task.update` before the turn ends.
- Never use `task.*` to describe work abstractly without actually persisting it when the user asked for real backlog changes.
- Never claim success without a successful `task.*` tool result in the current turn.

## Examples
- One AI task:
  create `task.create` with a self-contained prompt and `owner_type=ai_profile`.
- One flow with several sequential tasks:
  create `task.flow.create`, then create the first task, then create the second with `depends_on_task_ids=[first_task_id]`, then the third with `depends_on_task_ids=[second_task_id]`.
- Human handoff:
  call `task.update` on the current task with `owner_type=human`, `owner_ref=<person>`, and either `status=review`, `status=blocked`, or `status=todo` depending on why the handoff is happening.
- Add dependency later:
  call `task.dependency.add` when tasks were created separately and should now be linked in sequence.
- Investigate background run:
  call `task.run.list` for the task, then `task.run.get` for the specific run that matters.
- Inspect current board:
  call `task.board` with optional `owner_*`, `flow_id`, or `labels` filters to see counts and preview cards per column.
