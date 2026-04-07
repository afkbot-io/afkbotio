# Plugin System RFC

Date: 2026-04-07
Branch: `codex/plugin-system-rfc`
Status: design only

## 1. Problem

Сейчас AFKBOT уже умеет:

- регистрировать встроенные tool plugins через hardcoded registry: `afkbot/services/tools/plugins/__init__.py`
- грузить markdown skills через manifest-driven loader: `afkbot/services/skills/loader_service.py`
- устанавливать remote skills из marketplace/GitHub: `afkbot/services/skills/marketplace_service.py`
- грузить profile-local app modules c `register_apps(...)`: `afkbot/services/apps/registry_discovery.py`
- поднимать единый API runtime через FastAPI app factory: `afkbot/api/app.py`

Но у продукта нет первого класса для installable feature plugins:

- из отдельных репозиториев
- с версионированием и совместимостью
- с install/update/remove lifecycle
- с отдельным хранением вне core checkout
- с возможностью добавить backend router и web UI

Для канбан-интерфейса к Task Flow этого уже недостаточно. Profile-local apps и skill marketplace частично решают discovery/install, но не дают целостный продуктовый plugin runtime.

## 2. Recommendation

Лучший путь: делать не “произвольный Python import из git repo”, а отдельный `AFKBOT plugin runtime` поверх уже существующих patterns:

- manifest-first package contract
- filesystem install outside app source tree
- registry-driven runtime loading
- explicit install/enable/disable lifecycle
- capability-scoped surfaces: `tools`, `skills`, `apps`, `api_router`, `static_web`

Для первого релиза plugin system я рекомендую **embedded plugins**:

- plugin код грузится in-process
- plugin backend использует только зависимости, уже присутствующие в AFKBOT runtime
- frontend поставляется как готовый static build
- install идёт через source archive/local path, без `pip install` в shared env

Это прагматично и хорошо подходит именно для первого plugin `kanban`.

## 3. Почему не делать иначе

### 3.1 Не расширять `profiles/<id>/apps`

Profile-local app modules хороши как low-level extension point, но у них нет:

- install registry
- source/version metadata
- enable/disable state
- совместимости с версией AFKBOT
- web asset mounting
- plugin-level UX в CLI

Это механизм загрузки модулей, а не plugin product model.

### 3.2 Не использовать `skill marketplace` как plugin system

Skill marketplace уже умеет:

- source resolution
- remote fetch
- install into profile-local files

Но skills по своей природе:

- markdown/advisory or dispatch manifests
- не несут backend routers
- не несут web assets
- не являются durable feature modules

Marketplace code стоит переиспользовать для installer/source resolution, но не как конечную plugin модель.

### 3.3 Не делать сразу external-process plugins

Out-of-process plugins дают изоляцию и независимые зависимости, но резко увеличивают сложность:

- lifecycle orchestration
- ports/process management
- auth между core и plugin service
- health checks
- update/restart semantics

Для первого plugin `kanban` это лишнее. External-process mode можно оставить как v2/v3.

## 4. Plugin Types

### 4.1 Embedded plugin

Плагин хранится как unpacked package под runtime root и грузится core runtime напрямую.

Поддерживаемые surfaces:

- `skills_dir`
- `apps`
- `tools`
- `api_router`
- `static_web`

### 4.2 External service plugin

Будущий режим, не v1.

Поддерживаемые surfaces:

- MCP endpoint
- HTTP service
- external web app URL

Этот режим нужен позже, если plugin требует отдельные Python/NPM deps, sandboxing или независимый deploy cycle.

## 5. V1 Scope

V1 plugin system должен решить только это:

1. скачать plugin из отдельного repo или локального пути
2. провалидировать manifest и совместимость
3. установить пакет под runtime root
4. записать install record
5. enable/disable plugin globally или per profile
6. на `afk start` загрузить plugin router/static assets
7. дать plugin возможность зарегистрировать tools/apps/skills

Что сознательно не надо делать в v1:

- arbitrary post-install shell hooks
- isolated per-plugin virtualenv
- plugin-specific DB migrations
- plugin marketplace catalog
- dynamic CLI command injection
- unsigned community plugin trust model

## 6. Storage Model

Плагины должны храниться **вне app source tree**, внутри runtime root.

Recommended layout:

```text
<root_dir>/
  plugins/
    registry.json
    packages/
      kanban/
        1.0.0/
          .afkbot-plugin/
            plugin.json
          backend/
          web/
          skills/
```

Дополнительно нужен persistent install registry.

### 6.1 V1 registry format

Для v1 достаточно file-backed registry:

- `root_dir/plugins/registry.json`

Почему не SQLite сразу:

- installer и startup loader проще дебажить
- plugin install state логически ближе к runtime config, чем к product data
- можно перейти на DB позже без ломки package format

Recommended record shape:

```json
{
  "plugins": [
    {
      "plugin_id": "kanban",
      "version": "1.0.0",
      "source_kind": "github_archive",
      "source_ref": "github:afkbot-io/afkbotkanbanplugin@v1.0.0",
      "install_path": "plugins/packages/kanban/1.0.0",
      "enabled": true,
      "profiles": ["default"],
      "installed_at": "2026-04-07T10:00:00Z",
      "manifest": {}
    }
  ]
}
```

Если позже понадобится richer state, можно заменить registry file на:

- `plugin_install`
- `plugin_profile_binding`

Но для старта это не обязательно.

## 7. Package Contract

Каждый plugin repo должен иметь manifest:

```text
.afkbot-plugin/plugin.json
```

Почему JSON:

- language-neutral
- удобен для remote install validation
- легко описывается Pydantic contract
- не привязан только к Python-specific tooling

### 7.1 Manifest fields

Required:

- `plugin_id`
- `name`
- `version`
- `afkbot_version`
- `kind`
- `entrypoint`

Recommended:

- `description`
- `author`
- `homepage`
- `repository`
- `license`
- `capabilities`
- `mounts`

Example:

```json
{
  "plugin_id": "kanban",
  "name": "Task Flow Kanban",
  "version": "1.0.0",
  "afkbot_version": ">=1.0.7,<2.0.0",
  "kind": "embedded",
  "entrypoint": "backend.plugin:register",
  "description": "Kanban web interface for Task Flow.",
  "capabilities": {
    "api_router": true,
    "static_web": true,
    "tools": false,
    "skills": false,
    "apps": false
  },
  "mounts": {
    "api_prefix": "/v1/plugins/kanban",
    "web_prefix": "/plugins/kanban"
  },
  "paths": {
    "backend_root": "backend",
    "web_root": "web/dist",
    "skills_root": "skills"
  }
}
```

## 8. Python Entrypoint Contract

Plugin entrypoint должен быть deterministic registration hook, не arbitrary lifecycle hook.

Recommended interface:

```python
def register(registry: PluginRuntimeRegistry) -> None:
    ...
```

`PluginRuntimeRegistry` должен уметь:

- `register_router(...)`
- `register_static_mount(...)`
- `register_tool_factory(...)`
- `register_app_module(...)`
- `register_skill_dir(...)`

Важно: entrypoint не должен:

- сам поднимать сервер
- запускать background loops
- писать в env/shared venv
- делать network install side effects

Он только регистрирует declarative surfaces.

## 9. Runtime Loading

На `afk start` core должен:

1. прочитать plugin registry
2. выбрать enabled plugins
3. провалидировать `afkbot_version`
4. импортировать их entrypoints
5. собрать merged runtime surfaces
6. передать их в:
   - API app factory
   - tool registry builder
   - app registry merge
   - skill loader overlay

### 9.1 API integration

`afkbot/api/app.py` сейчас только включает core routers.

Нужно расширить до:

- `create_app(plugin_manager=...)`
- include plugin routers
- mount plugin static directories

Это лучший путь для kanban plugin, потому что:

- один хост и один auth perimeter
- нет второго web server
- нет cross-origin проблем

### 9.2 Tool integration

Сейчас tool plugins hardcoded в `_PLUGIN_FACTORIES`.

Нужно перейти к merged registry:

- builtin factories
- plugin factories

При этом builtin source должен остаться primary, а plugin names должны быть namespaced:

- `kanban.*`
- `github.*`
- `jira.*`

Это уменьшает collision risk.

### 9.3 Skills integration

Skill loader уже умеет core + profile-local roots.

Нужно расширить его до:

- core skills
- plugin-provided skill dirs
- profile-local skills

Порядок precedence:

1. always/core mandatory
2. plugin skills
3. profile-local overrides

Или, если хочется fail-closed поведения, не разрешать plugin override core skill names.

## 10. Installer

Нужно добавить отдельный CLI namespace:

- `afk plugin install <source>`
- `afk plugin list`
- `afk plugin inspect <plugin_id>`
- `afk plugin enable <plugin_id> [--profile ...]`
- `afk plugin disable <plugin_id> [--profile ...]`
- `afk plugin update <plugin_id>`
- `afk plugin remove <plugin_id>`

### 10.1 Supported sources in v1

- local path
- GitHub repo archive
- explicit GitHub tarball/zipball URL

Best implementation:

- reuse source parsing/fetch patterns from `afkbot/services/skills/marketplace_service.py`
- prefer GitHub archive download over `git clone`
- unpack into temp dir
- validate manifest
- copy into runtime plugin packages dir
- update registry atomically

### 10.2 Dependency policy

V1 embedded plugins не должны тянуть произвольные Python зависимости.

Это ключевое ограничение.

Почему:

- shared venv dependency conflicts
- runtime reproducibility
- upgrade complexity
- security surface

Если plugin нужны свои зависимости, это уже future external-service plugin mode.

## 11. Security Model

V1 security model должен быть explicit and honest:

- plugin install = trusted code execution
- unsigned plugins are allowed only through explicit operator install
- plugin repo source and version must be persisted
- plugin cannot auto-enable itself
- plugin capabilities must be declared in manifest

Не стоит делать вид, что embedded community plugins безопасны. Они не sandboxed.

## 12. Compatibility Rules

Manifest должен иметь:

- `version`
- `afkbot_version`

Recommended policy:

- reject install if incompatible with current core version
- keep only one active version per `plugin_id` in v1
- allow reinstall/update by replacing active version

## 13. Why Kanban Should Be a Plugin

Kanban UI:

- опционален
- heavy on UI iteration
- не нужен каждому runtime
- логически сидит поверх `Task Flow`, а не внутри него

Это делает его идеальным первым plugin candidate.

## 14. Recommended Implementation Order

### Phase 1

- plugin manifest contract
- plugin registry file + installer
- plugin manager
- API router/static mount support

### Phase 2

- plugin tool factory registry
- plugin skill dirs
- plugin app modules

### Phase 3

- plugin marketplace/catalog
- signed/trusted sources
- external service plugins

## 15. Final Recommendation

Лучший вариант для AFKBOT:

- делать `embedded plugin system` как productized layer поверх уже существующих `skills marketplace`, `app registry discovery`, `tool registry`, `FastAPI app factory`
- не использовать shared `pip install` для plugin deps в v1
- не тащить dynamic CLI injection в первую версию
- первый plugin `kanban` делать как:
  - plugin backend router
  - static SPA
  - zero extra Python deps
  - reuse existing `TaskFlowService`

Это даст рабочую и расширяемую механику без слома core runtime.
