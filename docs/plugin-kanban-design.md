# Kanban Plugin Design

Date: 2026-04-07
Branch: `codex/plugin-system-rfc`
Status: design only

See also: `docs/plugin-kanban-implementation-plan.md`

## 1. Goal

Сделать первый installable plugin:

- отдельный repo
- отдельный release cycle
- web interface для существующего `Task Flow`
- без изменения доменной модели `task/task_flow/task_run/task_event`

То есть plugin должен **использовать текущий Task Flow**, а не дублировать его.

Проверено по текущему core:

- `Task Flow` board уже есть
- append-only task comments уже есть
- review/inbox/events/runs уже есть

Значит основной объём работы для kanban идёт в plugin runtime, web facade и UI, а не в новую task domain model.

## 2. Why Plugin, Not Core

Kanban UI:

- продуктово опционален
- потребует быстрой итерации по UX
- привносит frontend bundle и API facade
- не является минимально необходимой частью headless/runtime core

Поэтому его лучше держать отдельно от main repo.

## 3. Plugin Repo Shape

Recommended repo layout:

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
    dist/
      index.html
      assets/...
  README.md
```

`plugin.json`:

- `plugin_id = "kanban"`
- `kind = "embedded"`
- `entrypoint = "backend.plugin:register"`
- `api_prefix = "/v1/plugins/kanban"`
- `web_prefix = "/plugins/kanban"`

## 4. Runtime Model

Kanban plugin должен состоять из двух частей.

### 4.1 Backend

Тонкий FastAPI router, который вызывает существующие core services:

- `TaskFlowService`
- `TaskFlowCliService` только если нужен operator-style maintenance payload

Backend не должен:

- иметь свою task domain model
- иметь свою очередь
- иметь собственные runtime workers

### 4.2 Frontend

Статический SPA bundle, который AFKBOT main API server раздаёт по:

- `/plugins/kanban`

Frontend общается с backend router по:

- `/v1/plugins/kanban/...`

Рекомендованный стек для первого plugin:

- Vite + React
- plain CSS / component CSS
- тот же visual language, что и в `afkbotweb`

То есть не Tailwind admin panel, а тот же dark glass/neon стиль с `Plus Jakarta Sans`, `Cormorant Garamond`, `JetBrains Mono` и общими цветами/радиусами.

## 5. Core UX

Минимальный UX для v1:

- board page
- filters
- task drawer
- create task modal
- quick move between columns
- comments/events/runs view

### 5.1 Board columns

Колонки должны 1:1 повторять current `Task Flow` statuses:

- `todo`
- `blocked`
- `running`
- `review`
- `completed`
- `failed`
- `cancelled`

Не нужно invent-ить новый kanban status model.

### 5.2 Filters

Обязательные:

- `profile_id`
- `flow_id`
- `owner`
- `labels`
- `overdue only`
- `review for me`

### 5.3 Task card

На карточке достаточно:

- title
- owner
- priority
- due date
- labels
- dependency badge
- review badge
- overdue marker

### 5.4 Task drawer

В drawer должны открываться:

- task details
- comments
- event trail
- run history
- dependencies / dependents

Это ключевое отличие нормального product UI от “просто board”.

Комментарии не нужно придумывать заново: current core уже хранит их append-only через `task.comment.add/list`. Plugin должен просто сделать их нормальной частью UI и подтолкнуть AI/runtime оставлять их consistently.

## 6. Backend Endpoints

Так как в core пока нет dedicated Task Flow REST API, plugin должен дать thin facade.

Recommended endpoints:

### Read

- `GET /v1/plugins/kanban/board`
- `GET /v1/plugins/kanban/tasks`
- `GET /v1/plugins/kanban/tasks/{task_id}`
- `GET /v1/plugins/kanban/tasks/{task_id}/events`
- `GET /v1/plugins/kanban/tasks/{task_id}/comments`
- `GET /v1/plugins/kanban/tasks/{task_id}/runs`
- `GET /v1/plugins/kanban/review`
- `GET /v1/plugins/kanban/inbox`

### Write

- `POST /v1/plugins/kanban/tasks`
- `PATCH /v1/plugins/kanban/tasks/{task_id}`
- `POST /v1/plugins/kanban/tasks/{task_id}/comments`
- `POST /v1/plugins/kanban/tasks/{task_id}/review/approve`
- `POST /v1/plugins/kanban/tasks/{task_id}/review/request-changes`
- `POST /v1/plugins/kanban/tasks/{task_id}/dependencies`
- `DELETE /v1/plugins/kanban/tasks/{task_id}/dependencies/{depends_on_task_id}`

### Maintenance

- `GET /v1/plugins/kanban/stale`
- `POST /v1/plugins/kanban/stale/sweep`

## 7. Mapping UI Actions to Core Semantics

### 7.1 Drag card to another column

Это не должно быть “blind status rewrite”.

Правила:

- `todo -> blocked`: allowed, but reason required
- `todo/blocked -> review`: reviewer or human owner must be present
- `running`: read-only for drag in v1
- `completed/failed/cancelled`: drag disabled in v1

Лучше ограничить write semantics, чем сделать board, который ломает runtime invariants.

### 7.2 Reassign to human

UI action “Assign to human” должен маппиться на:

- `owner_type = "human"`
- `owner_ref = ...`
- status:
  - `todo`, если работа должна начаться человеком
  - `review`, если это handoff на проверку
  - `blocked`, если нужны explicit changes

### 7.3 Reassign to AI profile

UI action “Assign to AI” должен маппиться на:

- `owner_type = "ai_profile"`
- `owner_ref = <profile_id>`

Если задача в `todo`, её подхватит существующий background runtime.

## 8. Refresh Model

Для v1 я не рекомендую websocket-first board.

Лучше:

- poll every 5-10 seconds
- support `since_event_id` or `updated_after`
- redraw only changed cards

Почему:

- быстрее вывести в прод
- меньше moving parts
- достаточно для первого plugin

WebSocket streaming можно добавить после стабилизации REST model.

## 9. Auth

Kanban plugin должен жить в том же auth perimeter, что и основной API.

Лучший вариант:

- использовать existing AFKBOT connect/session auth
- не поднимать отдельный login flow
- не разносить UI и API по разным origin

Тогда plugin UI на `/plugins/kanban` работает с тем же API server.

## 10. Permissions

Plugin должен уважать те же профильные границы, что и core services.

Нельзя:

- показывать задачи других профилей без explicit profile selection
- скрыто делать operator writes без тех же checks, что есть в core CLI/tool paths

Kanban plugin должен быть thin UI layer, а не second policy engine.

## 11. What Not To Add In V1

Не надо в первый kanban plugin тащить:

- real-time collaborative editing
- custom status system
- plugin-specific DB schema
- own task scheduler
- own review engine
- custom notification system

Всё это уже есть или должно оставаться в core.

## 12. Recommended Delivery Plan

### Step 1

Сделать core plugin runtime:

- installer
- registry
- plugin manifest
- router/static mounting

### Step 2

Сделать `kanban` plugin repo:

- backend router
- static SPA
- board + task drawer + filters

### Step 3

Добавить operator polish:

- comments/events/runs tabs
- review actions
- stale repair actions

### Step 4

После стабилизации:

- live updates
- bulk edits
- saved views

## 13. Final Recommendation

Первый `kanban` plugin лучше реализовать как:

- **embedded plugin**
- **static SPA + FastAPI router**
- **без своих Python deps**
- **без своей БД**
- **поверх существующего Task Flow service layer**

Это даст:

- нормальный installable plugin format
- отдельный repo и release cycle
- быстрый первый product win
- минимум риска для core runtime
