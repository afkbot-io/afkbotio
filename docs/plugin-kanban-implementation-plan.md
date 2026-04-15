# Plugin + Kanban Implementation Plan

Date: 2026-04-07
Branch: `codex/plugin-system-rfc`
Status: planning only

## Current Validated State

### What already exists in core

`Task Flow` in `origin/main` is already much richer than the initial assumption:

- durable task domain: `task`, `task_flow`, `task_dependency`, `task_run`, `task_event`
- board/report read model: `afkbot/services/task_flow/service.py`
- append-only comments: `task.comment.add` / `task.comment.list`
- review actions, inbox, stale repair, event history, run history
- detached runtime for AI-owned tasks

Relevant files:

- `afkbot/services/task_flow/service.py`
- `afkbot/services/task_flow/contracts.py`
- `afkbot/services/task_flow/runtime_service.py`
- `afkbot/services/task_flow/context_overrides.py`
- `afkbot/services/tools/plugins/task_comment_add/plugin.py`
- `afkbot/services/tools/plugins/task_comment_list/plugin.py`
- `afkbot/skills/taskflow/SKILL.md`

Conclusion: the kanban work should reuse current `Task Flow` and not change its domain model.

### What does not exist yet

- installable plugin runtime
- plugin registry and installer
- plugin router/static mounting in the main FastAPI app
- web-facing REST facade for `Task Flow`
- kanban UI
- enforced AI comment discipline for task execution

### What `afkbotweb` actually uses today

The current platform web repo is a Vite + React app with custom CSS, not Tailwind/shadcn:

- React 18 + Vite
- plain component CSS files
- visual tokens in `src/App.css`
- fonts:
  - `Plus Jakarta Sans`
  - `Cormorant Garamond`
  - `JetBrains Mono`
- dark glass/neon visual language:
  - `--bg-deep: #070A0F`
  - `--panel-glass: rgba(255,255,255,0.04)`
  - `--stroke: rgba(255,255,255,0.09)`
  - `--neon-cyan: #00E5FF`
  - `--neon-violet: #A855F7`

Relevant files in `afkbotweb`:

- `src/App.css`
- `src/components/Navbar/Navbar.css`
- `src/components/DocsPage/DocsPage.css`
- `src/components/Cases/Cases.css`
- `src/components/Automations/Automations.css`

Conclusion: the kanban plugin should use the same stack and the same visual primitives, not introduce Tailwind or a second design language.

## Problem Statement

Users need an installable way to add optional product surfaces to AFKBOT without bloating the core repo. The first concrete need is a kanban web UI over the existing `Task Flow`, where board columns reflect current task statuses, cards update automatically as AI/background execution changes state, owners are visible as AI profile vs human, and every task has durable comments that AI can leave for handoff, progress, and review.

JTBD:

- When I run AFKBOT operationally, I want optional feature modules to install cleanly from separate repos, so I can add product surfaces like kanban without forking core.
- When I manage AI and human work together, I want to see the existing backlog as a live board, so I can understand what is running, blocked, waiting for review, or done.
- When AI hands off work, I want durable comments attached to the task, so human collaborators see context without reading raw events or terminal output.

## 3 Variants

### 1. Build kanban directly into core

Pros:

- simplest runtime model
- no plugin loader work
- fewer moving parts for v1

Cons:

- UI bundle and optional UX iteration become core concerns
- no reusable plugin mechanism for future extensions
- harder to keep kanban release cycle independent

Impact: medium  
Confidence: high  
Effort: medium

### 2. Embedded plugin system with kanban as first plugin

Pros:

- matches the product goal of installable optional features
- keeps core runtime and domain logic separate from UI product surface
- allows kanban to live in its own repo and release independently
- reuses current in-process service layer and auth perimeter

Cons:

- requires plugin manifest, registry, install lifecycle, and runtime loading
- plugin code is trusted in-process code, not sandboxed

Impact: high  
Confidence: high  
Effort: medium

### 3. Separate standalone kanban app outside AFKBOT plugin runtime

Pros:

- cleanest repo separation
- independent frontend/backend stack
- easy to evolve separately

Cons:

- second deployment unit
- separate auth/session story
- duplicate API surface and likely extra backend glue
- does not solve the general plugin problem

Impact: medium  
Confidence: medium  
Effort: medium-large

## Recommendation

Build `v1` as an embedded plugin system and ship the kanban UI as the first plugin.

Why this is the right cut:

- `Task Flow` domain is already implemented; the missing layer is packaging and UI.
- The first plugin can stay thin:
  - backend router over existing `TaskFlowService`
  - static SPA bundle
  - no own DB
  - no own scheduler
- It creates a reusable installation model for later plugins, instead of hardcoding one special kanban path.

## Metrics

Primary:

- time to first successful plugin install
- time to first visible task on board
- percentage of AI task handoffs that include at least one durable comment

Secondary:

- median board refresh latency
- review turnaround time after AI hands off to human
- percentage of kanban actions completed without CLI fallback

Guardrail:

- plugin install failure rate
- plugin load failures on `afk start`
- policy regressions where a plugin bypasses existing task/profile boundaries
- board refresh load against SQLite/runtime

## MVP

### Must-have

- core plugin manifest and registry
- plugin install/list/inspect/enable/disable/remove CLI
- plugin runtime loader in the main process
- plugin router mounting in FastAPI
- plugin static asset mounting
- first plugin repo: `kanban`
- kanban board with the existing Task Flow columns:
  - `todo`
  - `blocked`
  - `running`
  - `review`
  - `completed`
  - `failed`
  - `cancelled`
- owner badges for:
  - `ai_profile`
  - `human`
- task drawer with:
  - details
  - comments
  - events
  - runs
  - dependencies
- polling-based live refresh
- comment composer in UI
- core prompt/runtime policy so AI is expected to leave durable task comments

### Nice-to-have

- optimistic drag-and-drop
- saved filters/views
- inline review actions from drawer and card
- board delta endpoint instead of full board poll
- bulk moves and bulk assignment

### Out-of-scope for v1

- websocket collaboration
- standalone plugin process
- per-plugin Python dependency installation
- plugin-specific DB migrations
- custom status model different from Task Flow
- separate login/auth system for kanban

## Risks

- In-process plugins are trusted code. That is acceptable only with explicit operator install and clear manifest metadata.
- If the kanban UI invents its own status semantics, it will drift from Task Flow invariants and break the runtime model.
- If AI comments are left to prompt text alone, adoption may be inconsistent.
- If the plugin directly depends on `afkbotweb` source at runtime, packaging will become brittle.
- If board refresh pulls too much data too often, SQLite-backed local setups may feel sluggish.

## Next Steps

1. Land core plugin runtime in AFKBOT.
2. Add Task Flow web facade and AI comment policy changes in core.
3. Create separate `kanban` plugin repo with backend and static web bundle.
4. Integrate the kanban UI against the current Task Flow service layer.
5. Add smoke tests for install, mount, board load, comment flow, and live refresh.

## Architecture

## 1. Core plugin runtime in AFKBOT

Recommended new package:

- `afkbot/services/plugins/`

Recommended modules:

- `contracts.py`
- `manifest.py`
- `registry_file.py`
- `installer.py`
- `source_fetch.py`
- `manager.py`
- `loader.py`
- `runtime_registry.py`

Recommended manifest location in plugin repos:

- `.afkbot-plugin/plugin.json`

Recommended surfaces for `v1`:

- `api_router`
- `static_web`
- `tools`
- `skills`
- `apps`

## 2. Core AFKBOT touch points

Files that should change in the core repo:

- `afkbot/settings.py`
  - add plugin roots and feature flags
- `afkbot/api/app.py`
  - mount plugin routers and static bundles
- `afkbot/services/tools/plugins/__init__.py`
  - merge builtin tool factories with plugin factories
- `afkbot/services/skills/loader_service.py`
  - add plugin-provided skill roots
- `afkbot/services/apps/registry_discovery.py`
  - allow plugin app modules as another discovery source
- `afkbot/cli/commands/`
  - add `plugin.py`

Recommended runtime storage:

- `<root_dir>/plugins/registry.json`
- `<root_dir>/plugins/packages/<plugin_id>/<version>/...`

## 3. Kanban plugin repo

Recommended repo:

- `afkbot-plugin-kanban`

Recommended layout:

```text
afkbot-plugin-kanban/
  .afkbot-plugin/
    plugin.json
  backend/
    plugin.py
    router.py
    service.py
    contracts.py
  web/
    package.json
    src/
    dist/
  README.md
```

Plugin manifest should declare:

- `plugin_id = "kanban"`
- `kind = "embedded"`
- `entrypoint = "backend.plugin:register"`
- `api_prefix = "/v1/plugins/kanban"`
- `web_prefix = "/plugins/kanban"`

## 4. Kanban backend model

The plugin backend should be a thin facade over core services.

Use these existing capabilities instead of new domain logic:

- `TaskFlowService.build_board(...)`
- `list_tasks(...)`
- `get_task(...)`
- `update_task(...)`
- `list_task_comments(...)`
- `add_task_comment(...)`
- `list_task_events(...)`
- `list_task_runs(...)`
- `approve_review_task(...)`
- `request_review_changes(...)`
- `add_dependency(...)`
- `remove_dependency(...)`
- `build_human_inbox(...)`

Recommended endpoints:

- `GET /v1/plugins/kanban/board`
- `GET /v1/plugins/kanban/tasks`
- `GET /v1/plugins/kanban/tasks/{task_id}`
- `GET /v1/plugins/kanban/tasks/{task_id}/comments`
- `POST /v1/plugins/kanban/tasks/{task_id}/comments`
- `GET /v1/plugins/kanban/tasks/{task_id}/events`
- `GET /v1/plugins/kanban/tasks/{task_id}/runs`
- `PATCH /v1/plugins/kanban/tasks/{task_id}`
- `POST /v1/plugins/kanban/tasks/{task_id}/review/approve`
- `POST /v1/plugins/kanban/tasks/{task_id}/review/request-changes`
- `POST /v1/plugins/kanban/tasks/{task_id}/dependencies`
- `DELETE /v1/plugins/kanban/tasks/{task_id}/dependencies/{depends_on_task_id}`

## 5. Kanban frontend stack and design system

The plugin UI should match the current AFKBOT web language.

Recommended stack:

- Vite
- React 18
- plain CSS or CSS modules
- same font families and token names as `afkbotweb`

Do not use in `v1`:

- Tailwind
- shadcn
- unrelated admin-dashboard theme kits

Reason:

- the current platform already has a clear visual language
- matching it is easier if the plugin uses the same primitives
- plain CSS is already the proven stack in `afkbotweb`

### Design translation from `afkbotweb`

Use these direct patterns:

- docs-style sticky top bar
- glass panels with subtle blur
- neon cyan as primary action/accent
- violet as secondary accent
- mono pills for machine/status metadata
- serif italic only for sparse accent moments, not for dense board text

### Recommended kanban layout

- sticky top bar
- collapsible filter rail or compact filter strip
- horizontally scrollable board
- equal-height status columns
- task drawer from the right

### Recommended card composition

- title
- owner badge
- priority chip
- due date chip
- label pills
- dependency count
- review badge if needed
- latest activity timestamp

### Owner badge semantics

- `ai_profile:<profile_id>`
  - mono cyan-accent badge
  - robot/agent icon optional
- `human:<owner_ref>`
  - mono neutral badge
  - user icon optional

### Column behavior

The board columns must reflect existing Task Flow semantics exactly:

- `todo`
- `blocked`
- `running` for both `claimed` and `running`
- `review`
- `completed`
- `failed`
- `cancelled`

No plugin-specific status mapping.

## 6. Live refresh model

For `v1`, use polling, not websocket streaming.

Recommended behavior:

- board poll every 5 seconds when visible
- slow down to 15-30 seconds when the tab is hidden
- task drawer details poll every 5-10 seconds while open
- re-render only changed columns/cards locally

This is enough to satisfy:

- task status changes from detached AI runs
- cards moving between columns automatically
- comments/events/runs appearing without reload

Websocket support can come later if needed.

## 7. Comment strategy

### Current state

Core already has append-only task comments.

What is missing is not storage, but behaviour and UI discipline.

### Recommendation

Implement comments on two layers.

Layer 1: policy

- update `afkbot/services/task_flow/context_overrides.py`
- update `afkbot/skills/taskflow/SKILL.md`

Make AI explicitly responsible for adding comments when it:

- starts meaningful work that may outlive one run
- hands off to human
- requests review
- blocks on missing information
- completes substantial work and the result summary matters

Layer 2: guardrail

In `afkbot/services/task_flow/runtime_service.py`, add a fallback path:

- if a detached run ends in `review`, `blocked`, `completed`, or `failed`
- and the run did not create any task comment
- persist one runtime-generated comment using the final run summary or error text

This avoids silent handoffs where only `task_event` exists but no human-readable note exists in comments.

### Recommended comment types

The schema already allows free-form `comment_type`, so no migration is needed.

Standardize these values in docs and prompts:

- `progress_note`
- `handoff`
- `review_summary`
- `review_feedback`
- `blocked_reason`
- `completion_summary`

## 8. UX behaviour rules

### Drag and drop

Do not allow blind status rewriting.

Recommended rules:

- `todo -> blocked`
  - allowed
  - reason required
- `todo/blocked -> review`
  - allowed only if reviewer or human target is set
- `running`
  - read-only in `v1`
- `completed/failed/cancelled`
  - read-only in `v1`

### Reassign to AI

Set:

- `owner_type = ai_profile`
- `owner_ref = <profile_id>`

If status is `todo`, detached runtime will pick it up.

### Reassign to human

Set:

- `owner_type = human`
- `owner_ref = <person>`

Then choose status deliberately:

- `todo` for direct human ownership
- `review` for approval/review
- `blocked` when changes or input are needed

## 9. Delivery plan

### Phase 1. Core plugin runtime

Deliver in AFKBOT core:

- manifest contract
- registry file
- installer
- enable/disable lifecycle
- API router/static mount support

### Phase 2. Task Flow web facade and comment policy

Deliver in AFKBOT core:

- plugin-friendly Task Flow router mounting support
- comment policy updates in prompt/skill
- fallback runtime comment guardrail

### Phase 3. Kanban plugin backend

Deliver in plugin repo:

- router
- DTOs
- service glue over core `TaskFlowService`
- board/tasks/comments/events/runs endpoints

### Phase 4. Kanban plugin frontend

Deliver in plugin repo:

- board
- filters
- task drawer
- comment composer
- review actions
- polling refresh

### Phase 5. Polish

- optimistic moves
- saved filters
- bulk actions
- board delta endpoint

## 10. Release readiness for the first usable cut

The first release candidate is ready when all of these are true:

- operator can install the kanban plugin from a separate repo
- `afk start` mounts the plugin UI and API
- board reflects live Task Flow statuses
- AI-owned tasks move across columns without page reload
- owner badge clearly distinguishes AI profile vs human
- drawer shows comments, events, and runs
- human can leave comments in UI
- AI handoff leaves at least one durable comment per task

