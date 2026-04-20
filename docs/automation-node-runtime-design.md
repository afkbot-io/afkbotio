# Automation Node Runtime Design

Status: draft
Date: 2026-04-19
Branch: `codex/automation-node-runtime`

## 1. Problem Statement

Сейчас automation subsystem в AFKBOT решает только trigger-ingress задачу:

- `webhook` и `cron` хранятся в `afkbot/models/automation.py`
- `webhook` execution в `afkbot/services/automations/webhook_execution.py`
- `cron` execution в `afkbot/services/automations/cron_execution.py`
- оба пути в итоге собирают одно сообщение и отправляют его в AgentLoop через `compose_webhook_message(...)` / `compose_cron_message(...)` из `afkbot/services/automations/message_factory.py`

Фактически сегодня модель такая:

`trigger -> sanitize/dedupe -> compose prompt -> LLM`

Это хорошо для простых случаев, но плохо для следующих сценариев:

- JSON нужно валидировать до LLM;
- поля нужно нормализовать и категоризовать детерминированно;
- после нескольких одинаковых webhook payload'ов хочется один раз зафиксировать преобразование в коде, а не повторно платить токенами;
- логика ветвления (`if`, `switch`, `fan-out`) не должна жить только внутри промпта;
- часть шагов должна быть кодовой, часть условной, часть AI-driven;
- нужна возможность постепенно “вынести” стабильную post-processing логику из промпта в reusable node.

Желаемая модель:

`trigger -> deterministic/code nodes -> optional AI node -> actions/tasks/output`

Ключевая идея: **automation должен стать graph-based runtime для событий, а не только prompt launcher для LLM**.

## 2. Current Repo Baseline

Что уже есть и на что нужно опираться:

- отдельный automation runtime c claims, leases и isolated session ids;
- webhook dedupe через `event_hash` в `afkbot/services/automations/payloads.py`;
- trusted prompt overlay для automation turns в `afkbot/services/automations/context_overrides.py`;
- отдельный `Task Flow` subsystem с durable tasks/runs/comments/events;
- зафиксированная в repo граница: `Task Flow` не должен переопределять automation semantics.

Что важно не сломать:

- `automation` остаётся trigger-oriented subsystem;
- `Task Flow` остаётся work-item subsystem;
- node-based automation не должен тайно превращаться в Task Flow v2;
- интеграция с `Task Flow` допустима только через явные node types вроде `task.create` / `task.delegate`.

## 3. JTBD

### Primary job

Как оператор платформы, я хочу собирать automation flow из trigger и node-ов, чтобы:

- дешёвые и предсказуемые шаги выполнялись кодом;
- дорогие и неоднозначные шаги выполнялись AI только там, где это действительно нужно;
- flow был наблюдаемым, версионируемым и пригодным к повторному запуску.

### Secondary jobs

Как AI/agent внутри AFKBOT, я хочу:

- видеть повторяющиеся payload patterns;
- предлагать deterministic node draft для нормализации/валидации/роутинга;
- запускать его в shadow mode;
- после подтверждения уменьшать будущий token spend.

Как оператор, я хочу:

- понимать, почему событие ушло в конкретную ветку;
- видеть вход/выход каждого node;
- откатывать неудачную node version без правки промпта вручную.

## 4. External Analogs And What To Borrow

### n8n

Полезные паттерны:

- `Code` node умеет запускать custom JavaScript/Python как workflow step: [n8n Code node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.code/)
- у code node есть режимы `Run Once for All Items` и `Run Once for Each Item`, что полезно для batch vs per-event semantics
- `Switch` node даёт multi-output routing: [n8n Switch](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.switch/)
- `Schedule Trigger` поддерживает cron-style trigger: [n8n Schedule Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/)
- `Sub-workflows` позволяют собирать modular reusable flows и дают parent/sub-execution tracing: [n8n Sub-workflows](https://docs.n8n.io/flow-logic/subworkflows/)
- `Merge` подтверждает необходимость явного fan-in: [n8n Merge](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.merge/)

Что не стоит копировать:

- чрезмерную low-code магию без ясного contract surface;
- смешивание editor-level convenience с production-grade runtime guarantees.

### Zapier

Полезные паттерны:

- code steps как trigger и action: [Use Python code in Zaps](https://help.zapier.com/hc/en-us/articles/8496326417549-Use-Python-code-in-Zaps)
- reusable mini-flows через Sub-Zaps, но это beta feature: [Understanding Sub-Zaps](https://help.zapier.com/hc/en-us/articles/32283713627533-Understanding-Sub-Zaps)
- simple schedule surface: [Schedule by Zapier](https://help.zapier.com/hc/en-us/articles/8496288648461-Schedule-Zaps-to-run-at-specific-intervals)

Полезный анти-паттерн:

- `Paths` в Zapier ограничены и не позволяют общий shared tail после ветвления без дублирования или Sub-Zap: [Paths limitations](https://help.zapier.com/hc/en-us/articles/8496288555917-Add-branching-logic-to-Zaps-with-Paths)

Вывод:

- branching model нужно проектировать так, чтобы после `switch` можно было нормально merge-ить execution обратно;
- иначе пользователи будут дублировать post-processing steps.

### Pipedream

Это сильный референс для `code steps + control flow + inspectability`, но не для полностью произвольного graph engine:

- code steps прямо в workflow на Node.js, Python, Go, Bash: [Pipedream code overview](https://pipedream.com/docs/workflows/building-workflows/code)
- triggers включают HTTP/webhook и schedule: [Pipedream triggers](https://pipedream.com/docs/workflows/building-workflows/triggers)
- `Switch` умеет branch + merge обратно в parent flow: [Pipedream Switch](https://pipedream.com/docs/workflows/building-workflows/control-flow/switch)
- `Parallel` подтверждает multi-branch fan-out/fan-in и export обратно в parent flow: [Pipedream Parallel](https://pipedream.com/docs/workflows/building-workflows/control-flow/parallel)
- code можно публиковать как reusable actions между workflows: [Sharing code across workflows](https://pipedream.com/docs/workflows/building-workflows/code/nodejs/sharing-code)
- `Inspect` и `Event History` подтверждают run/step observability: [Inspect Events](https://pipedream.com/docs/workflows/building-workflows/inspect/), [Event History](https://pipedream.com/docs/workflows/event-history/)

Главный takeaway:

- **code steps плюс явные control-flow nodes** намного полезнее, чем один большой prompt box.

### Trigger.dev

Полезные паттерны для надёжности:

- durable task model с child task triggering: [Trigger.dev tasks overview](https://trigger.dev/docs/tasks/overview)
- task-level idempotency keys: [Trigger.dev idempotency](https://trigger.dev/docs/idempotency)
- declarative и imperative schedules: [Trigger.dev scheduled tasks](https://trigger.dev/docs/tasks/scheduled)
- run-level observability, logging и structured execution lifecycle: [Trigger.dev Runs](https://trigger.dev/docs/runs), [Trigger.dev Logging](https://trigger.dev/docs/logging), [How it works](https://trigger.dev/docs/how-it-works)

Главный takeaway:

- runtime должен иметь first-class idempotency, retries, child runs и structured observability.

### Kestra

Полезные паттерны:

- inline Python script прямо в flow config или `.py` file execution: [Kestra Python in flows](https://kestra.io/docs/how-to-guides/python)
- `Subflows` подтверждают reusable child-flow pattern: [Kestra Subflows](https://kestra.io/docs/workflow-components/subflows)
- `Flowable` tasks подтверждают branching/composition semantics: [Kestra Tasks](https://kestra.io/docs/workflow-components/tasks), [Kestra Flowable tutorial](https://kestra.io/docs/tutorial/flowable)

Вывод:

- для AFKBOT v1 логично сделать Python-first code nodes и explicit child-flow/subagent nodes.

### Node-RED

Полезные базовые строительные блоки:

- `Function` node для кода;
- `Switch` node для routing;
- `Change` node для простых mutation without code.
- `Subflows` и `Link` patterns подтверждают reusable graph fragments: [Node-RED Subflows](https://nodered.org/docs/user-guide/editor/workspace/subflows), [Flow structure](https://nodered.org/docs/developing-flows/flow-structure)
- `Status` node подтверждает value of evented observability/debug path: [Node-RED Status](https://nodered.org/docs/creating-nodes/status)

Источник: [Node-RED core nodes](https://nodered.org/docs/user-guide/nodes)

Вывод:

- базовый palette должен состоять не из сотни интеграций, а из нескольких сильных primitives плюс нормальный debug/status surface.

### Langflow

Полезный AI-паттерн:

- custom components как Python code;
- assistant может помогать создавать custom component из natural language.

Источник: [Langflow custom components](https://docs.langflow.org/components-custom-components)

Вывод:

- AI-generated node в AFKBOT должен быть именно `drafted code component`, а не только “невидимая магия”.

### What The Market Actually Proves

Надёжно подтверждаются такие паттерны:

- graph branching + explicit merge;
- code steps as first-class workflow artifacts;
- subflows / child runs / reusable workflow fragments;
- run-level and step-level observability;
- idempotent child execution and retries.

### What Is AFKBOT-Specific Innovation

Это уже не market-standard, а продуктовые гипотезы AFKBOT:

- `observe-first` learning loop;
- automatic node extraction from repeated AI/tool behavior;
- template-generated node drafts by AI;
- shadow-promotion loop;
- `resume_with_ai_if_safe` fallback.

В RFC это нужно трактовать именно как AFKBOT-specific design, а не как уже доказанный рынком стандарт.

## 5. Solution Options

### Option A. Visual-first low-code builder

Описание:

- делаем canvas как в n8n;
- node-ы mostly declarative;
- code node остаётся escape hatch.

Плюсы:

- быстро воспринимается операторами;
- красиво продаётся как product surface;
- легко объяснить branching.

Минусы:

- слишком рано упрёмся в “один универсальный JSON editor”;
- тяжело выразить сложную логику и versioning;
- слабее reuse story;
- высокий риск сделать editor раньше runtime.

Effort: L  
Confidence: medium

### Option B. Code-first node runtime with thin visual layer

Описание:

- node = versioned executable artifact;
- canvas и UI только отображают graph, contracts и run trace;
- deterministic steps живут в Python nodes;
- AI node отдельный explicit node type;
- AI может предлагать/generate node drafts, но runtime остаётся code-driven.

Плюсы:

- соответствует сильным сторонам AFKBOT как local AI runtime;
- проще сделать production-safe semantics;
- легче тестировать, version-control и reuse;
- токены снижаются через постепенный extraction стабильной логики в code nodes.

Минусы:

- initial UX менее “no-code friendly”;
- понадобится нормальный contract layer и node packaging.

Effort: M  
Confidence: high

### Option C. Full durable workflow engine with own scheduler/backfill/queue model

Описание:

- строим почти Temporal-lite / Kestra-lite внутри AFKBOT.

Плюсы:

- максимум гибкости;
- strongest enterprise story.

Минусы:

- слишком дорого для текущего этапа;
- конфликтует с уже существующими `automation` и `Task Flow`;
- большой риск архитектурного раздвоения.

Effort: XL  
Confidence: low

## 6. Recommendation

Рекомендуется **Option B: code-first node runtime with thin visual layer**.

Причины:

- AFKBOT уже силён в runtime/orchestration, а не в low-code editor-first UX;
- текущий automation subsystem уже умеет claims, leases, session isolation и trusted overlays;
- Python-first nodes естественно встраиваются в текущий Python codebase;
- такую систему можно постепенно улучшать AI-assisted node synthesis без потери контроля.

Стратегическая формула:

`webhook/cron/poll trigger + code-first graph runtime + explicit AI nodes + reusable node registry`

## 7. Proposed Product Model

### 7.1 Existing automation stays

`Automation` в текущем виде остаётся верхним trigger descriptor:

- `webhook`
- `cron`
- позже `poll`

Но появляется новый optional execution mode:

- `mode = "prompt"`: current behavior
- `mode = "graph"`: new node-based behavior

Важно:

- `graph` mode живёт **внутри существующего automation subsystem**;
- webhook/cron ingress, claims, leases, dedupe и isolated sessions переиспользуются;
- отдельный parallel graph daemon не нужен.

### 7.2 New product term

Пользовательский термин:

- `Automation Flow`

Внутренние primitives:

- `automation`
- `automation_flow`
- `automation_node`
- `automation_edge`
- `automation_run`
- `automation_node_run`
- `node_definition`
- `node_version`
- `automation_optimization_snapshot`

### 7.3 Trigger model

v1 trigger types:

- `webhook`
- `cron`
- `poll`

`poll` концептуально похож на cron, но отличается тем, что:

- хранит cursor/state;
- умеет detection new/changed events;
- сам готовит event envelope для downstream nodes.

## 8. Core Architecture

### 8.1 High-level execution path

```text
Trigger
  -> ingress envelope
  -> idempotency / dedupe
  -> payload sample capture
  -> graph executor
  -> node run(s)
  -> optional AI node(s)
  -> action/output/task handoff
  -> run ledger + observability
```

### 8.2 Separation of concerns

#### Trigger layer

Отвечает за:

- scheduling / webhook ingress / polling cadence;
- dedupe;
- input envelope creation;
- initial run record.

#### Graph runtime

Отвечает за:

- topological execution;
- branching;
- merge;
- retries;
- timeout;
- node permissions;
- run trace.

#### Node adapter layer

Отвечает за привязку graph node к уже существующему или новому runtime:

- `builtin` nodes: router/merge/stop and other graph-native primitives;
- `code` nodes: Python artifact execution;
- `ai` nodes: bounded AgentLoop inference step;
- `agent` nodes: adapter над существующим `SubagentService`;
- `task` nodes: adapter над `TaskFlowService`;
- `action` nodes: adapter над `app/http/tool` surfaces.

То есть graph executor оркестрирует единый `Node Protocol`, но внутренне dispatch-ит в разные adapters, а не пытается исполнять всё как `main.py`.

#### AI assistance layer

Отвечает за:

- sample analysis;
- draft node suggestion;
- schema inference suggestion;
- prompt-to-node code generation;
- drift detection suggestion.

AI assistance **не должна** по умолчанию silently deploy-ить code в production flow.

## 9. Domain Model

### `automation_flow`

- `id`
- `automation_id`
- `name`
- `status`
- `entry_node_ids_json`
- `default_timeout_sec`
- `default_retry_policy_json`
- `version`
- `created_at`
- `updated_at`

### `automation_node`

- `id`
- `flow_id`
- `node_key`
- `node_kind`
- `node_type`
- `category`
- `definition_id`
- `definition_version`
- `position_x`
- `position_y`
- `config_json`
- `input_mapping_json`
- `enabled`
- `created_at`
- `updated_at`

### `automation_edge`

- `id`
- `flow_id`
- `from_node_id`
- `to_node_id`
- `port_key`
- `condition_label`
- `created_at`

### `node_definition`

Reusable logical component definition.

- `id`
- `key`
- `name`
- `node_kind`
- `runtime`
- `language`
- `category`
- `description`
- `input_schema_json`
- `output_schema_json`
- `config_schema_json`
- `permission_profile_json`
- `created_at`
- `updated_at`

### `node_version`

Executable immutable artifact.

- `id`
- `definition_id`
- `version`
- `source_code`
- `requirements_json`
- `checksum`
- `created_by_type`
- `created_by_ref`
- `origin`
- `created_at`

`origin` values:

- `manual`
- `template`
- `ai_draft`
- `ai_promoted`

Note:

- `node_version.source_code` applies only to `code` nodes;
- `agent`, `task`, `action`, and `ai` nodes use adapter contracts instead of arbitrary Python source.

### `automation_run`

- `id`
- `automation_id`
- `flow_id`
- `trigger_type`
- `trigger_event_key`
- `status`
- `input_payload_json`
- `normalized_payload_json`
- `started_at`
- `finished_at`
- `session_id`
- `error_code`
- `error_text`

### `automation_node_run`

- `id`
- `automation_run_id`
- `node_id`
- `node_version_id`
- `status`
- `attempt`
- `branch_key`
- `input_json`
- `output_json`
- `stdout_text`
- `stderr_text`
- `started_at`
- `finished_at`
- `error_code`
- `error_text`

## 9.1 Graph Topology

Automation flow должен проектироваться сразу как **directed acyclic graph**, а не как линейная цепочка.

Это значит:

- у одной node может быть несколько outgoing edges;
- у одной node может быть несколько incoming edges;
- graph поддерживает fan-out и fan-in;
- routing идёт через named ports, а не через неявные связи.

### Topology rules

- graph is acyclic in v1;
- хотя бы один entry node;
- dangling edges запрещены;
- merge node обязан явно указывать merge strategy;
- terminal branch должен заканчиваться `stop`, `action`, `task`, `ai`, или `merge`.

### Branch model

Одна node может вести в несколько downstream node-ов.

Примеры:

- `if.condition` -> `true`, `false`
- `switch.condition` -> `billing`, `support`, `default`
- `validator` -> `default`, `error`
- `split.batch` -> много branch execution через `item`

Каждый branch должен иметь:

- `branch_key`
- `parent_node_run_id`
- `branch_input_hash`

Это нужно для dedupe, retries и traceability.

### Merge model

Минимальные merge strategies:

- `all_completed`
- `first_completed`
- `collect_outputs`
- `collect_successful_ignore_errors`

Пример topology:

```text
webhook.trigger
  -> json.validate
  -> switch.condition
      -> "billing" -> field.map -> http.request -> merge.join
      -> "support" -> task.create -> merge.join
      -> "unknown" -> ai.classify -> merge.join
  -> ai.summarize
```

## 10. Node Taxonomy

v1 palette должен быть маленьким и сильным.

### Trigger-adjacent

- `webhook.trigger`
- `cron.trigger`
- `poll.trigger`

### Deterministic data nodes

- `json.validate`
- `json.normalize`
- `field.map`
- `field.extract`
- `dedupe.check`
- `payload.hash`
- `regex.match`
- `template.render`

### Control-flow nodes

- `if.condition`
- `switch.condition`
- `split.batch`
- `merge.join`
- `stop`

### Integration/action nodes

- `http.request`
- `shell.command` only if policy allows
- `task.create`
- `task.comment`
- `task.delegate`
- `app.run`

### Agent nodes

- `subagent.run`
- later: `subagent.spawn`
- later: `subagent.await`

### AI nodes

- `ai.classify`
- `ai.extract`
- `ai.summarize`
- `ai.decide`
- `ai.route`

### Meta nodes

- `sample.capture`
- `shadow.compare`
- `drift.detect`

## 11. Runtime Choice

### Recommendation for v1

Сделать **Python-first code-node runtime**.

Причины:

- AFKBOT core уже Python;
- самый низкий operational complexity;
- легче интегрировать policy, sandboxing, logging и packaging;
- проще писать node templates и tests;
- user request прямо допускает Python scripts как code-node unit.

Важно:

- это относится только к `code` nodes;
- `subagent` nodes переиспользуют текущий `SubagentService`;
- `task` nodes переиспользуют `TaskFlowService`;
- `ai` nodes переиспользуют AgentLoop/runtime overlays;
- `action` nodes переиспользуют app/tool/http adapters.

### JavaScript support

Поддержку JS лучше планировать как v2/v3 через explicit isolated runtime:

- отдельный subprocess;
- versioned runtime image;
- clear package install rules.

Не стоит в v1 делать dual-runtime surface, если она удвоит сложность packaging и security review.

### AI-last default

Даже если конечный flow всё равно потом идёт в ИИ-подсистему, default execution policy должна быть такой:

`trigger -> deterministic nodes -> optional tool/action nodes -> AI node`

Не наоборот.

Это важно по трём причинам:

- если действие можно сделать детерминированно, ИИ не должен каждый раз заново “придумывать” tool call;
- любое повторяемое преобразование payload должно вытесняться из prompt в code node;
- ИИ должен получать уже нормализованный, сжатый и безопасный контекст, а не сырой шумный webhook.

То есть system prompt остаётся важным, но **LLM должен работать поверх уже подготовленного execution state**, а не быть основным механизмом оркестрации всего подряд.

## 12. Node Contract Specification

У graph executor должен быть единый **внешний Node Protocol**, но не все node-ы исполняются одинаково внутри.

Нужно различать:

- внешний protocol contract, который одинаков для graph executor;
- внутренний execution adapter, который зависит от `node_kind`.

`Node Protocol` не заменяет `ToolBase` и не дублирует tool/plugin surface.
Это отдельный contract именно для automation graph runtime.

### Node kinds

Recommended `node_kind` values:

- `builtin`
- `code`
- `ai`
- `agent`
- `task`
- `action`

### Adapter mapping

- `builtin` -> graph-native executor
- `code` -> Python code runtime
- `ai` -> bounded AgentLoop call
- `agent` -> `SubagentService`
- `task` -> `TaskFlowService`
- `action` -> app/http/tool adapter layer

### `code` node manifest

Каждый executable `code` node должен иметь manifest и predictable function signature.

### Suggested manifest

```json
{
  "key": "json.normalize.customer_webhook",
  "name": "Normalize Customer Webhook",
  "runtime": "python",
  "entrypoint": "main.py:run",
  "category": "transform",
  "input_schema": {
    "type": "object"
  },
  "output_schema": {
    "type": "object"
  },
  "config_schema": {
    "type": "object"
  },
  "permissions": {
    "network": false,
    "filesystem": false,
    "shell": false
  },
  "timeout_sec": 10,
  "retry_policy": {
    "max_attempts": 2
  }
}
```

### Suggested Python signature for `code` nodes

```python
from typing import Any


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    payload = ctx["input"]
    config = ctx["config"]
    metadata = ctx["metadata"]

    normalized = {
        "customer_id": str(payload["customer"]["id"]),
        "email": payload["customer"]["email"].strip().lower(),
    }

    return {
        "output": normalized,
        "ports": ["default"],
        "metrics": {"normalized_fields": 2},
    }
```

### Execution context

`ctx` должен включать:

- `input`
- `config`
- `metadata`
- `run`
- `trigger`
- `secrets` via explicit refs only
- `artifacts` for temp files if allowed

### Return contract

Каждый node должен возвращать:

- `output`
- `ports`
- optional `metrics`
- optional `artifacts`
- optional `annotations`

Это нужно для нормального branch/merge semantics.

### Canonical result shape

Для согласованности executor/tests/result contract должен использовать один формат:

```json
{
  "ok": true,
  "ports": ["default"],
  "output": {
    "customer_id": "42",
    "email": "a@example.com"
  },
  "error_code": null,
  "reason": null,
  "metadata": {
    "duration_ms": 12,
    "deterministic": true
  }
}
```

Для v1 лучше не смешивать `port` и `ports`.
Внешний protocol должен использовать только `ports`.

### `agent` node contract

`subagent.run` нельзя моделировать как обычный Python artifact.

Это должен быть adapter-backed node с отдельным contract:

- `subagent_name`
- `result_schema`
- `allowed_capabilities`
- `timeout_sec`
- `max_turns`
- `side_effect_policy`
- `fallback_policy`
- `idempotency_mode`

Recommended v1 semantics:

- graph executor делает `run -> wait -> result`;
- `agent` node по умолчанию blocking для parent branch;
- default retries for `agent` nodes = `0`;
- `subagent.spawn` / `subagent.await` можно добавить позже как explicit advanced pattern.

### `task` node contract

`task` nodes являются единственной allowed surface для durable Task Flow mutations.

Examples:

- `task.create`
- `task.comment`
- `task.delegate`
- `task.review.request`

### `ai` node semantics

`AI node` и `subagent node` не одно и то же.

- `AI node` = bounded inference step над входом графа, без child orchestration
- `subagent node` = child agent session с собственным runtime, session id, wait/result and capability profile

### Fixed authoring interface for AI-written nodes

Чтобы максимально снизить вероятность ошибок, ИИ не должен писать node “с нуля в пустоту”.

Он должен писать node только по фиксированному host interface:

- один manifest;
- один entrypoint;
- один predictable return shape;
- ноль скрытых side effects по умолчанию;
- одна тестовая обвязка стандартного формата.

Рекомендуемый package shape:

```text
node_package/
  manifest.json
  main.py
  fixtures/
    sample_input.json
    expected_output.json
    invalid_input.json
  tests/
    test_contract.py
    test_samples.py
    test_negative.py
```

### Canonical node archetypes

ИИ должен генерировать ноды не “как получится”, а выбирать один из заранее заданных archetype templates.

v1 archetypes:

- `validator`
- `mapper`
- `normalizer`
- `router`
- `aggregator`
- `ai-prep`
- `task-handoff`

Эти archetypes относятся к `code` nodes.
`agent`, `task`, `action`, and `ai` nodes должны собираться из adapter contracts, а не из свободного Python template.

Пример мысли:

- если задача “проверить JSON и выбросить ошибку”, это `validator`
- если “переименовать/переложить поля”, это `mapper`
- если “свести хаотичные payload в канонический shape”, это `normalizer`
- если “развести по кейсам”, это `router`

Это резко снижает пространство ошибок, потому что AI сначала выбирает archetype, а потом заполняет шаблон, а не генерирует произвольный runtime contract.

### Example generated node skeleton

```python
from __future__ import annotations

from typing import Any


def run(ctx: dict[str, Any]) -> dict[str, Any]:
    payload = ctx["input"]

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        return {
            "output": {
                "error_code": "missing_customer",
                "error_text": "customer object is required",
            },
            "ports": ["error"],
            "annotations": {"deterministic": True},
        }

    email = customer.get("email")
    if not isinstance(email, str) or not email.strip():
        return {
            "output": {
                "error_code": "invalid_email",
                "error_text": "customer.email must be non-empty string",
            },
            "ports": ["error"],
            "annotations": {"deterministic": True},
        }

    return {
        "output": {
            "customer_id": str(customer["id"]),
            "email": email.strip().lower(),
        },
        "ports": ["default"],
        "metrics": {"normalized": 2},
        "annotations": {"deterministic": True},
    }
```

### Why template-first generation matters

Если ИИ генерирует node по шаблону, а не свободным стилем, то система автоматически получает:

- стабильный import path;
- predictable schema checks;
- predictable error port behavior;
- predictable test generation;
- predictable upgrade path.

Это и есть главный способ “максимально избежать ошибок”.

### Unified Node Protocol

Да, поверх всех node-ов нужен **единый protocol layer**, по духу похожий на MCP/tool contracts.

Это должен быть не просто “шаблон Python-файла”, а именно host protocol:

- versioned descriptor;
- JSON-safe params/input schema;
- explicit output schema;
- fixed error model;
- fixed port model;
- fixed metadata/telemetry hooks.

То есть любая node в системе, независимо от назначения, должна выглядеть для рантайма одинаково.

Рекомендуемая форма descriptor:

```json
{
  "protocol_version": 1,
  "node_name": "json.normalize.customer_webhook",
  "display_name": "Normalize Customer Webhook",
  "description": "Normalize incoming customer webhook payload",
  "archetype": "normalizer",
  "input_schema": {
    "type": "object"
  },
  "config_schema": {
    "type": "object"
  },
  "output_schema": {
    "type": "object"
  },
  "ports": ["default", "error"],
  "error_codes": ["missing_customer", "invalid_email"],
  "determinism": "deterministic",
  "permissions": {
    "network": false,
    "filesystem": false,
    "shell": false
  }
}
```

### Why MCP-like protocol is the right idea

Если у node есть protocol-level descriptor, AFKBOT получает ровно то, что уже хорошо работает в MCP/tool bridge стиле:

- единый способ валидировать вход;
- единый способ показывать capability ИИ;
- единый способ строить auto-generated code;
- единый способ строить auto-generated tests;
- единый способ возвращать structured errors и telemetry.

По сути это должен быть **Node Protocol**, а не просто “куски Python”.

Если у всех node-ов один внешний protocol и разные, но строго оформленные internal adapters, ИИ действительно сможет “всегда одинаково” их разрабатывать без смешения доменов.

## 13. Execution Semantics

### 13.1 Branching

`switch` и `if` должны возвращать named ports:

- `true`
- `false`
- `default`
- custom named cases

### 13.2 Merge

Нужно поддержать merge после branches уже в v1.

Иначе AFKBOT повторит ограничение Zapier Paths, где общий tail после branching неудобен.

Минимальный merge modes:

- `first_completed`
- `all_completed`
- `collect_outputs`
- `collect_successful_ignore_errors`

### 13.3 Terminal branch semantics

Ветка считается корректно завершённой, если она пришла в одно из состояний:

- `stop`
- successful action node
- successful task handoff node
- successful AI node
- merge node, который выпустил branch дальше

### 13.4 Graph executor policy

Graph executor должен:

- запускать ready node-ы в topological order;
- разрешать parallel execution только для branch-safe node types;
- хранить `branch_key` на каждом node run;
- блокировать merge, пока не выполнится выбранная merge strategy;
- уметь short-circuit terminal branches.
- dispatch-ить node execution через adapter registry, а не через один универсальный executor.

### 13.5 Idempotency

Automation flow run должен иметь:

- trigger-level idempotency key;
- node-level idempotency key;
- branch-level dedupe for fan-out runs.

Правило:

- если `webhook event_key` уже обработан, повторный ingress не создаёт новый `automation_run`;
- если node marked idempotent и input hash совпадает, можно вернуть cached output.

### 13.6 Retries

Retry policy должна задаваться на трёх уровнях:

- flow default
- node override
- trigger override

### 13.7 Timeouts

Timeout также нужен:

- flow-level hard timeout;
- node-level execution timeout;
- AI-node tighter token/runtime budget than generic code nodes.

## 14. AI-Assisted Node Synthesis

Это differentiator, но здесь нужен строгий control model.

### 14.1 What AI should do

AI может:

- анализировать несколько успешных webhook runs;
- находить повторяющиеся deterministic transformations;
- предлагать JSON schema;
- генерировать draft node code;
- предлагать switch cases;
- предлагать field categories и mappings;
- предлагать extraction of prompt segment into a code node.

### 14.2 What AI should not do by default

AI не должен по умолчанию:

- silently publish new node version into live flow;
- менять permissions node-а;
- подключать network/shell доступ без явного approval;
- удалять AI node и заменять его code node без traceable review.

### 14.3 Recommended lifecycle

```text
Observe payloads
  -> infer repeated structure
  -> create draft node
  -> run in shadow mode
  -> compare output vs current AI result
  -> show diff/confidence
  -> human approve
  -> promote node version
```

### 14.4 Shadow mode

Shadow mode обязателен для AI-generated nodes:

- existing live path continues unchanged;
- draft node runs side-by-side;
- result difference is stored;
- promotion allowed only after stability threshold.

Suggested threshold:

- at least 20 samples;
- at least 95% identical or accepted-equivalent outputs;
- zero security violations.

### 14.5 AI node generation lifecycle

Рекомендуемый pipeline для ИИ:

```text
1. Observe recent runs
2. Cluster similar payloads
3. Pick archetype
4. Infer input/output schema draft
5. Generate node from template
6. Generate tests from fixtures
7. Run static checks
8. Run contract tests
9. Run golden tests
10. Run negative tests
11. Run shadow mode against historical samples
12. Produce promotion report
```

ИИ не должен переходить к шагу 12, если шаги 6-11 не зелёные.

### 14.6 What AI is actually allowed to write

AI может писать:

- pure transformation logic;
- validation rules;
- branching predicates;
- output shaping;
- task handoff payload builders.

AI не должен в v1 свободно писать:

- произвольный shell orchestration;
- dynamic dependency installers;
- uncontrolled network crawlers;
- code with hidden file writes outside allowed temp/artifact dirs.

Это должно быть enforced не только policy, но и generation policy.

### 14.7 Promotion rule

Promotion из `ai_draft` в `ai_promoted` возможен только если:

- manifest schema valid;
- entrypoint imports cleanly;
- contract tests green;
- golden tests green;
- negative tests green;
- shadow comparison above threshold;
- permissions unchanged from approved template;
- promotion report signed as approved by human or explicit workspace policy.

Без этого AI draft остаётся draft навсегда.

## 15. Autonomous Validation And Testing

Автоматическая генерация node имеет смысл только если рядом есть **автоматическая система валидации**.

Иначе будет просто pipeline генерации багов.

### 15.1 Test layers

Каждый AI-generated node должен автоматически проходить 5 слоёв проверки.

#### Layer 1. Static validation

- `manifest.json` schema validation
- python parse/import check
- forbidden import scan
- permission manifest consistency check

#### Layer 2. Contract tests

Проверяют runtime contract:

- `run(ctx)` exists
- return shape contains `output` and `ports`
- ports are declared and valid
- output is JSON-serializable
- no forbidden side effects occurred

#### Layer 3. Golden sample tests

Проверяют happy path на реальных payload samples:

- input fixture -> expected output
- input fixture -> expected port
- stable metrics/annotations where needed

#### Layer 4. Negative tests

Проверяют controlled failure behavior:

- missing required fields
- wrong types
- malformed nested structures
- oversized payload
- unsafe content in strings

Node должен не “падать как попало”, а возвращать controlled error output или `error` port.

#### Layer 5. Shadow replay

Проверяет node на исторических реальных событиях:

- replay on redacted production samples
- compare with current live path or AI result
- compute divergence report
- block promotion if divergence too high

### 15.2 Generated test files

ИИ должна генерировать не только `main.py`, но и обязательный test bundle.

Минимум:

- `tests/test_contract.py`
- `tests/test_samples.py`
- `tests/test_negative.py`

Пример направления:

```python
from __future__ import annotations

import json
from pathlib import Path

from main import run


def _load(name: str):
    path = Path(__file__).resolve().parent.parent / "fixtures" / name
    return json.loads(path.read_text())


def test_sample_happy_path() -> None:
    result = run({"input": _load("sample_input.json"), "config": {}, "metadata": {}})
    assert result["ports"] == ["default"]
    assert result["output"] == _load("expected_output.json")


def test_invalid_input_goes_to_error_port() -> None:
    result = run({"input": _load("invalid_input.json"), "config": {}, "metadata": {}})
    assert result["ports"] == ["error"]
    assert result["output"]["error_code"] == "invalid_email"
```

### 15.3 Test harness in AFKBOT

Для самого продукта нужен встроенный node test harness service:

- поднимает isolated subprocess;
- подставляет fixture input;
- ловит stdout/stderr;
- валидирует manifest и return contract;
- возвращает machine-readable report.

Внутренне это должно быть ближе к существующим repo harness patterns в `tests/services/.../_harness.py` и стандартному `pytest` flow, а не к ad-hoc shell script.

### 15.4 Required report format

Система тестирования должна отдавать не “что-то сломалось”, а нормальный structured payload.

Пример:

```json
{
  "ok": false,
  "stage": "golden_tests",
  "node_key": "json.normalize.customer_webhook",
  "summary": "2 of 7 tests failed",
  "failures": [
    {
      "test_name": "test_sample_happy_path",
      "error_code": "assertion_failed",
      "message": "customer_id was missing from output",
      "expected": {"customer_id": "42", "email": "a@example.com"},
      "actual": {"email": "a@example.com"}
    }
  ],
  "stdout": "",
  "stderr": "",
  "artifacts": []
}
```

### 15.5 Error taxonomy

Для node generation и testing нужен фиксированный taxonomy ошибок.

Минимальные коды:

- `manifest_invalid`
- `entrypoint_missing`
- `import_forbidden`
- `contract_invalid`
- `output_not_json_serializable`
- `schema_mismatch`
- `port_invalid`
- `runtime_timeout`
- `runtime_exception`
- `assertion_failed`
- `shadow_divergence_too_high`
- `permission_escalation_blocked`

Если taxonomy нет, то AI не сможет нормально чинить свои же node drafts.

### 15.6 Self-repair loop

После failed report ИИ может автоматически делать ограниченное число repair attempts:

```text
generate
  -> test
  -> read structured failure
  -> patch node
  -> rerun tests
```

Ограничения:

- max 2-3 repair attempts
- нельзя менять permissions во время self-repair
- нельзя менять expected outputs без explain/diff report

То есть ремонт допустим, но под контролем.

## 16. Adaptive Learning From Live Runs

Разделение automation на node-ы не должно включаться “сразу”.

Правильный режим:

- сначала automation работает как сейчас;
- система наблюдает реальные прогоны;
- потом строит гипотезы, что можно вынести в node;
- только после этого задаёт вопросы, предлагает extraction и запускает draft/shadow path.

### 16.1 Observe-first policy

Для каждой automation нужен режим `observe`:

- не меняет live execution path;
- собирает telemetry;
- строит optimization hints.

То есть у automation появляются состояния evolution:

- `prompt_only`
- `observe`
- `draft_split`
- `shadow_split`
- `promoted_graph`

Это лучше, чем сразу пытаться превратить любой prompt в граф.

### 16.2 What exactly should be observed

Нужно смотреть не только на payload, но и на то, как реально отрабатывает automation.

Полезные сигналы:

- входной payload shape;
- normalized payload keys;
- фрагменты prompt, которые повторяются;
- какие tool/app calls реально вызывает ИИ;
- с какими аргументами они вызываются;
- какие куски ответа/логики повторяются между запусками;
- где возникают retries, errors и ручные исправления;
- какие ветки решения фактически повторяются.

Это даёт материал не только для `validator`/`mapper`, но и для `router`/`task-handoff` nodes.

### 16.3 Do not store raw chain-of-thought

Идея смотреть “раздумья” ИИ полезна по смыслу, но сырое решение плохое.

Не стоит хранить raw hidden reasoning.

Вместо этого нужен **optimization trace**:

- prompt digest;
- runtime metadata;
- tool call sequence;
- action/result summary;
- error summary;
- reasoning summary card, если модель сама сформировала явное объяснение;
- repeated decision signatures.

То есть системе нужен не raw chain-of-thought, а **structured reasoning digest**.

Это:

- безопаснее;
- стабильнее;
- проще индексировать;
- проще использовать для автоматического извлечения node patterns.

### 16.4 Optimization snapshot

После каждого meaningful run automation должна сохранять `optimization_snapshot`.

Suggested fields:

- `automation_id`
- `run_id`
- `payload_signature`
- `prompt_digest`
- `tool_sequence_json`
- `decision_summary`
- `error_summary`
- `repeated_fragments_json`
- `candidate_patterns_json`
- `token_usage`
- `duration_ms`

Из этих snapshot'ов уже строится learning layer.

### 16.5 Candidate pattern extraction

На основе snapshots система строит hypotheses:

- “одни и те же 5 полей всегда нормализуются одинаково”
- “после этих признаков почти всегда вызывается один и тот же tool”
- “эти 3 кейса образуют стабильный switch”
- “этот prompt fragment можно заменить deterministic preprocessing node”

Каждая hypothesis должна иметь:

- `pattern_type`
- `confidence`
- `sample_count`
- `estimated_token_saving`
- `risk_level`
- `suggested_archetype`

### 16.6 When the system should ask questions

Вопросы должны возникать не всегда, а только в правильных местах.

Нужно спрашивать, если:

- confidence низкая;
- есть конфликтующие historical examples;
- node затрагивает irreversible side effects;
- требуется расширение permissions;
- неясны границы switch cases;
- есть drift в payload schema;
- expected outputs неочевидны.

Примеры хороших вопросов:

- “Правильно ли, что пустой `email` должен вести на `error` branch?”
- “Эти два payload вида считаются одним кейсом или разными?”
- “Можно ли этот повторяющийся tool call заменить deterministic HTTP node?”

### 16.7 Learning loop

Полный adaptive loop:

```text
run automation
  -> record optimization snapshot
  -> cluster similar runs
  -> detect repeated decision patterns
  -> form candidate node hypothesis
  -> ask questions only if ambiguity/risk is high
  -> generate draft node
  -> test
  -> shadow replay
  -> promote if approved
```

Это сильно лучше, чем one-shot decomposition.

## 17. Terminal Observability

До UI система должна быть нормально наблюдаема в терминале.

Это обязательная часть дизайна.

### 17.1 What should be visible in terminal

Оператор должен уметь увидеть:

- какие node-ы есть в flow;
- какие edges между ними;
- из какой node выходят какие ветки;
- какие node-ы реально отработали в конкретном run;
- где именно branch сломался;
- был ли fallback в AI path;
- какой output был у каждой node.

### 17.2 Suggested CLI surface

По аналогии с уже существующим `afk task board`, `afk task run-list`, `afk task event-list`, для automation graph стоит добавить terminal-first surface:

- `afk automation graph-show <automation_id>`
- `afk automation graph-validate <automation_id>`
- `afk automation run-list <automation_id>`
- `afk automation run-show <run_id>`
- `afk automation node-run-list <run_id>`
- `afk automation trace <run_id>`
- `afk automation explain <run_id>`

### 17.3 Suggested terminal output shapes

`graph-show`:

```text
automation: 42 customer-intake
mode: graph
entry: webhook.trigger

webhook.trigger
  -> json.validate [default]
json.validate
  -> switch.condition [default]
switch.condition
  -> field.map [billing]
  -> task.create [support]
  -> ai.classify [unknown]
field.map
  -> merge.join [default]
task.create
  -> merge.join [default]
ai.classify
  -> merge.join [default]
merge.join
  -> ai.summarize [default]
```

`trace`:

```text
run: 884
status: fallback_succeeded
trigger: webhook
event_key: evt_123

[1] webhook.trigger -> ok -> port=default
[2] json.validate -> ok -> port=default
[3] switch.condition -> ok -> port=billing
[4] field.map -> runtime_exception: missing amount
[5] fallback.ai_prompt_resume -> ok
```

### 17.4 Why terminal first matters

Если граф нельзя быстро прочитать в терминале, UI потом тоже будет плохим.

Terminal surface должен быть source of truth для:

- graph topology;
- runtime trace;
- fallback events;
- debugging and support.

## 18. Failure Handling And AI Fallback

Граф не должен быть brittle.

Если deterministic path сломался, automation не должна просто “умирать”, если политика разрешает деградацию в AI path.

### 18.1 Fallback modes

Для flow нужен explicit fallback policy:

- `fail_closed`
- `branch_error_only`
- `resume_with_ai`
- `resume_with_ai_if_safe`

### 18.2 Recommended default

Для большинства deterministic/action flows лучше default:

- `resume_with_ai_if_safe`

Для `agent` nodes лучше default:

- `fail_closed`, если child runtime не вернул structured safe outcome.

Это значит:

- если сломался deterministic node без irreversible side effects;
- и payload/trace можно безопасно передать дальше;
- runtime собирает `fallback prompt package`;
- и продолжает execution через существующий AgentLoop path.

### 18.3 Fallback prompt package

При fallback LLM должен получать не сырой event, а уже собранный пакет:

- original trigger payload
- successful node outputs before failure
- failed node name
- error_code / reason
- active branch path
- remaining intended objective
- safety note about what уже произошло и что повторять нельзя

Пример:

```text
Automation graph fallback.
- automation_id: 42
- run_id: 884
- failed_node: field.map
- error_code: missing_amount
- completed_nodes: webhook.trigger, json.validate, switch.condition
- branch_key: billing

Use the validated payload and completed node outputs below to finish the automation task safely.
Do not repeat already completed external actions.
```

Важно:

- `resume_with_ai_if_safe` это AFKBOT-specific behavior;
- это не нужно подавать как прямой market-standard pattern из аналогов.

### 18.4 When fallback is forbidden

Fallback в AI path нельзя делать, если:

- уже был irreversible external side effect и нет idempotency guarantee;
- policy запрещает AI continuation;
- ошибка связана с secret/permission boundary;
- trace package неполный и continuation небезопасна.
- failed node is `agent` and child runtime did not return structured `completed_actions` / `unsafe_actions_present` / `idempotency_keys` summary.

### 18.5 Result states

Итоговые run states должны различать:

- `succeeded`
- `failed`
- `fallback_started`
- `fallback_succeeded`
- `fallback_failed`

### 18.6 Structured child outcome for `agent` nodes

После `subagent` node parent graph должен получить не только `output`, но и structured child outcome:

- `completed_actions`
- `unsafe_actions_present`
- `idempotency_keys`
- `result_summary`
- `decision_digest`
- `fallback_safe`

Без этого fallback после `agent` node должен считаться unsafe.

## 19. What Will Be Added

Ниже то, что реально нужно добавить в систему.

### 19.1 New persisted entities

- `automation_flow`
- `automation_node`
- `automation_edge`
- `automation_run`
- `automation_node_run`
- `node_definition`
- `node_version`
- `automation_optimization_snapshot`

### 19.2 New core services

- `AutomationGraphService`
  - CRUD графа
  - graph validation
  - read models for terminal/UI
- `AutomationGraphExecutor`
  - topological execution
  - fan-out / fan-in
  - retries / timeouts
  - fallback triggering
- `AutomationNodeAdapterRegistry`
  - map `node_kind` to adapter
  - centralize capability boundaries
- `AutomationCodeNodeRuntime`
  - subprocess execution for `code` nodes only
  - Node Protocol validation
  - stdout/stderr capture
  - contract enforcement
- `AutomationSubagentNodeAdapter`
  - reuse `SubagentService`
  - `run -> wait -> result`
  - collect structured child outcome
- `AutomationAiNodeAdapter`
  - bounded AgentLoop step
  - reuse automation context overlays and runlog
- `AutomationTaskNodeAdapter`
  - explicit bridge to `TaskFlowService`
- `AutomationActionNodeAdapter`
  - explicit bridge to `app/http/tool` surfaces
- `AutomationOptimizationService`
  - observe-mode telemetry
  - candidate pattern extraction
  - draft generation requests

Важно:

- graph mode должен встраиваться в существующий automation ingress/runtime;
- не нужно делать отдельный parallel graph daemon поверх `runtime_daemon.py`.

### 19.3 New CLI/read models

- graph topology read model
- run trace read model
- node run ledger read model
- fallback event read model
- optimization insight read model

### 19.4 Repository organization rules

Чтобы не было костылей и дублей, слой должен быть таким:

- `services/automations/*`
  - ingress owner for webhook/cron/poll
  - prompt-vs-graph dispatch
- `services/automations/graph/*`
  - graph contracts
  - executor
  - adapter registry
  - read models
  - fallback
  - optimizer
- `services/subagents/*`
  - remains the only owner of persisted child-agent lifecycle
- `services/task_flow/*`
  - remains the only owner of durable backlog/work-item semantics
- `services/tools/*` and apps
  - remain the only owner of tool/app execution contracts

Rule:

- graph layer orchestrates;
- adapters delegate;
- domain owners stay where they already are.

## 20. User Cases

### Case 1. Billing webhook with branch split

Сейчас:

- приходит billing webhook
- ИИ читает сырой JSON
- решает, что это billing case
- руками вызывает tools

После внедрения:

- `webhook.trigger`
- `json.validate`
- `switch.condition`
  - `billing` -> `field.map`
  - `support` -> `task.create`
  - `unknown` -> `ai.classify`
- `field.map -> http.request -> ai.summarize`

Польза:

- billing идёт детерминированно;
- support сразу превращается в task;
- только неизвестные кейсы попадают в AI.

### Case 2. Automation learns and proposes node extraction

Сейчас automation 30 раз подряд:

- получает похожий payload
- нормализует одни и те же поля
- вызывает один и тот же tool

В observe-mode система замечает:

- повторяющийся mapping;
- устойчивый switch;
- одинаковый tool sequence.

После этого она предлагает:

- `normalizer` node draft
- `router` node draft
- вопрос: “Пустой `email` вести в `error` или `unknown`?”

После ответа:

- генерирует node
- тестирует
- прогоняет в shadow mode
- только потом предлагает promote.

### Case 3. Graph branch breaks but automation still completes

Flow:

- `webhook.trigger -> json.validate -> switch.condition -> field.map -> http.request`

Проблема:

- `field.map` падает на нестандартном payload.

Поведение:

- runtime пишет failed `automation_node_run`
- собирает fallback package
- запускает `resume_with_ai_if_safe`
- AI завершает automation, не повторяя уже завершённые шаги
- run получает статус `fallback_succeeded`

### Case 4. Subagent as a node

Flow:

- `webhook.trigger -> json.validate -> switch.condition`
- `complex_case -> subagent.run`
- `simple_case -> field.map -> http.request`

Поведение:

- `subagent.run` использует существующий `SubagentService`, а не исполняется как Python code artifact
- graph executor ждёт `run -> wait -> result`
- в `automation_node_run` сохраняются `subagent_task_id`, `child_session_id`, `child_run_id`
- parent trace умеет разворачивать child session

Польза:

- сложные ambiguous cases можно вынести в bounded child agent;
- при этом не появляется второй способ оркестровать child sessions вне уже существующего subagent runtime.

## 21. Compatibility And Non-Breakage Rules

Чтобы ничего не сломалось, нужны жёсткие правила.

### 21.1 Existing automations keep working

- `mode = prompt` остаётся дефолтом;
- текущие webhook/cron execution paths не переписываются принудительно;
- migration to graph is explicit.

### 21.2 Graph is additive

- graph runtime добавляется рядом с current prompt runtime;
- graph failure не меняет semantics старых automations;
- observe-mode не меняет live behavior.
- graph mode живёт внутри существующего automation ingress/runtime, а не в отдельном daemon.

### 21.3 Task Flow stays separate

- automation graph runs не пишутся в task tables;
- task nodes only call Task Flow APIs/services explicitly;
- task board не становится automation debugger.

### 21.4 Subagent stays separate

- `subagent` node не переопределяет `subagent_task`;
- `automation_node_run` только ссылается на child subagent execution;
- `subagent` node работает как adapter над существующим subagent lifecycle.

### 21.5 Fallback is controlled

- fallback не должен повторять side effects;
- fallback policy explicit per flow;
- fallback events всегда логируются в run trace.
- fallback after `agent` node requires structured child outcome, otherwise `fail_closed`.

### 21.6 Tool and Node contracts stay separate

- `ToolBase` remains the LLM/runtime tool contract;
- `Node Protocol` remains the graph executor contract;
- one must not replace the other.

### 21.7 Terminal read models before UI

- сначала стабильные CLI/read models;
- потом UI поверх тех же контрактов.

## 22. Polling Trigger Model

Polling не просто cron alias.

Нужны отдельные поля:

- `poll_interval_sec`
- `cursor_json`
- `lookback_window_sec`
- `backfill_mode`
- `max_items_per_tick`

Execution model:

```text
poll tick
  -> fetch source state
  -> compare with cursor
  -> create one event envelope per new item
  -> run graph per item or batch
  -> update cursor on successful checkpoint
```

Иначе polling будет слишком хрупким и неуправляемым.

## 23. Integration With Task Flow

Интеграция нужна, но только явная.

### Allowed

- node `task.create`
- node `task.comment`
- node `task.review.request`
- node `task.delegate`

### Not allowed

- трактовать каждый automation node как task;
- хранить automation graph runs в task tables;
- смешивать task board semantics с automation graph semantics.

Иначе:

- observability запутается;
- product boundaries поплывут;
- existing `Task Flow` RFC будет нарушен.

## 24. UI And Design System Direction

UI должен использовать уже зафиксированный AFKBOT web language, а не новый generic admin theme.

Опора на существующие design notes в repo:

- `Plus Jakarta Sans`
- `Cormorant Garamond`
- `JetBrains Mono`
- `--bg-deep: #070A0F`
- `--panel-glass: rgba(255,255,255,0.04)`
- `--stroke: rgba(255,255,255,0.09)`
- `--neon-cyan: #00E5FF`
- `--neon-violet: #A855F7`

### Canvas UX

Рекомендуемый layout:

- left rail: trigger/node palette
- center: graph canvas
- right inspector: config, schemas, permissions, run logs
- bottom panel: run trace / sample payload / diff

### Visual semantics

- deterministic/code nodes: cyan accent
- condition/switch nodes: amber accent
- AI nodes: violet accent
- task/action nodes: emerald accent
- failed runs: red edge glow
- shadow mode nodes: dashed violet outline

### Important UI rule

Нужно визуально отличать:

- deterministic node
- AI node
- draft AI-generated node
- production node

Иначе пользователь не поймёт, где у него твёрдая логика, а где модельная эвристика.

## 25. Security And Operational Risks

### Code execution risk

Риск:

- arbitrary Python in automation flow может быть опасен.

Mitigation:

- per-node permission manifest;
- deny-by-default network/filesystem/shell;
- isolated subprocess runtime;
- signed/versioned node artifacts.

### Secret leakage risk

Риск:

- node code и AI draft могут утянуть secrets в output/logs.

Mitigation:

- secret refs only;
- stdout/stderr redaction;
- no raw secret interpolation into source code.

### Hallucinated synthesis risk

Риск:

- AI создаст “почти работающий” node с ложной логикой.

Mitigation:

- draft + shadow + approval model;
- sample corpus requirement;
- golden tests before promotion.

### Graph complexity risk

Риск:

- пользователи начнут строить нечитаемые spaghetti flows.

Mitigation:

- subflows/reusable nodes;
- lint rules;
- complexity warnings;
- max depth / max node count alerts.

### Drift risk

Риск:

- external webhook payload schema поменялся, node silently устарел.

Mitigation:

- payload drift detection;
- schema mismatch alerts;
- auto-fallback to AI node if deterministic path fails and policy allows.

### Trace privacy risk

Риск:

- learning layer начнёт хранить слишком много “внутренностей” AI execution.

Mitigation:

- хранить structured optimization trace вместо raw hidden reasoning;
- redact sensitive values before persistence;
- retention policy for snapshots;
- explicit opt-in for deeper observability modes.

### Observability risk

Риск:

- без node-level run ledger оператор не поймёт, что сломалось.

Mitigation:

- separate `automation_node_run`;
- per-port execution trail;
- input/output snapshots with redaction;
- replay from node.

### Test harness quality risk

Риск:

- система тестирования будет выдавать useless ошибки вроде “test failed”.

Mitigation:

- structured failure payload;
- fixed error taxonomy;
- expected/actual diff;
- stage-aware reporting;
- no silent promotion on partial green.

### False confidence risk

Риск:

- green unit tests создадут ложное ощущение безопасности, хотя на реальных payload есть drift.

Mitigation:

- shadow replay обязателен;
- promotion based on historical samples, а не только fixtures;
- separate drift telemetry after deploy.

## 26. MVP Scope

### Must-have

- `mode = prompt|graph` for automations
- webhook and cron triggers
- Python-first code nodes
- basic node registry
- `json.validate`, `field.map`, `if`, `switch`, `merge`, `http.request`, `ai.classify`, `task.create`
- graph executor with retries/timeouts
- run ledger for flow and node runs
- sample capture
- AI draft node generation stored as draft only
- template-first node generation
- generated pytest-compatible contract/golden/negative tests
- structured test reports with fixed error taxonomy
- shadow replay before promotion
- unified `Node Protocol` descriptor/result format
- `observe` mode with optimization snapshots
- directed graph with fan-out/fan-in
- terminal graph/trace inspection
- controlled `resume_with_ai_if_safe` fallback
- `subagent` node as adapter over existing `SubagentService`
- structured child outcome for `agent` nodes

### Nice-to-have

- poll trigger
- reusable subflows
- shadow compare UI
- node templates marketplace
- JS runtime
- candidate-pattern question workflow

### Out of scope for v1

- BPMN editor
- full visual no-code integration catalog
- collaborative multi-user graph editing
- arbitrary package install from UI without review

## 27. Suggested Implementation Plan

### Phase 0. RFC and contracts

- finalize domain boundaries
- choose Python runtime contract
- add schema and manifest primitives
- define archetype templates and error taxonomy
- define `Node Protocol` descriptor/result contract
- define optimization snapshot contract
- define graph topology invariants
- define fallback prompt package contract
- define `node_kind` adapter mapping
- define `agent` node contract and child outcome schema

### Phase 1. Runtime foundation

- new models and repositories for flow/node/run
- graph executor
- Python node subprocess runtime
- node-level policies
- CLI/API for CRUD and run inspection
- node test harness and structured reports
- observe-mode telemetry capture
- candidate pattern extraction service
- terminal graph topology printer
- run trace reader and fallback ledger
- `subagent` node adapter over existing `SubagentService`
- persistence links to child sessions/runs

### Phase 2. Product surface

- minimal canvas UI
- run trace viewer
- payload sample explorer
- reusable node definitions
- test result viewer with expected/actual diffs
- optimization insights panel
- question/approval inbox for suggested node extraction

### Phase 3. AI-assisted extraction

- sample clustering
- prompt segment extraction suggestions
- draft node generation
- shadow mode
- approval workflow
- bounded self-repair loop from structured failures
- staged evolution: `prompt_only -> observe -> draft_split -> shadow_split -> promoted_graph`

### Phase 4. Advanced orchestration

- polling trigger
- subflows
- JS runtime
- caching / memoization / replay

## 28. Success Metrics

Primary:

- median tokens per automation run down
- share of runs finishing without entering AI node
- time-to-first-working-automation-flow
- successful run rate
- draft node pass rate on first generation
- percentage of useful node hypotheses generated from observe mode

Secondary:

- number of reusable nodes per workspace
- percentage of flows using deterministic pre-processing before AI
- mean debug time per failed run
- draft-to-promoted node conversion rate
- mean repair attempts per promoted node
- question-to-promotion conversion rate

Guardrails:

- no increase in security incidents
- no regression in webhook idempotency
- no confusion between automation and Task Flow surfaces
- no promotion with failing shadow replay
- no persistence of raw hidden reasoning by default
- no silent graph failure without trace or fallback event
- no hidden Task Flow mutations through `subagent` nodes

## 29. Final Recommendation

AFKBOT не должен становиться “ещё одним визуальным low-code builder”.

Правильная ставка для этого repo:

- сохранить текущие `webhook` / `cron` automations;
- добавить **graph execution mode**;
- сделать `code` nodes versioned Python artifacts, а остальные node kinds adapter-backed;
- ввести единый **Node Protocol**, похожий по духу на MCP/tool contracts;
- проектировать flow сразу как directed graph с branch/fan-out/fan-in;
- сделать terminal-first observability для graph topology и run trace;
- при поломке graph path уметь controlled fallback обратно в AI prompt path;
- оставить graph mode inside existing automation runtime, а не строить второй engine/daemon;
- сделать `subagent` отдельным `agent` node kind, который адаптирует текущий `SubagentService`;
- жёстко развести `Node Protocol`, `ToolBase`, `Task Flow` и `subagent_task`;
- заставить ИИ писать ноды только из archetype templates и fixed interface;
- заставить ИИ генерировать вместе с нодой обязательный test bundle;
- разрешать promotion только после static checks, contract tests, negative tests и shadow replay;
- сначала запускать automation в `observe` режиме и учиться на live runs;
- извлекать node hypotheses из optimization snapshots, а не делать one-shot decomposition;
- оставить `Task Flow` отдельным subsystem;
- дать AI право **предлагать и тестировать node drafts**, но не бесконтрольно публиковать их;
- строить UX вокруг observability, contracts и reusable code nodes, а не вокруг “магического” prompt box.

Коротко:

**лучший v1 для AFKBOT — это Pipedream-style code-first directed automation graph с Trigger.dev-style idempotency/run semantics, единым Node Protocol, observe-first learning loop, terminal-first observability, adapter-backed subagent/task/action nodes, controlled AI fallback, AI-last execution, template-based code-node generation и обязательным self-test pipeline, но без смешивания с Task Flow.**
