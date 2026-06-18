# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

## [1.9.22] - 2026-06-18

### Fixed

- Automation webhook/cron Task Flow tool calls now treat explicit
  `session_id: null` and `session_profile_id: null` as absent optional fields,
  so model-generated `task.create` or `task.update` payloads no longer fail with
  `task_session_binding_forbidden` when they are not actually trying to bind a
  session.
- Automation Task Flow tools still reject non-null explicit session bindings, so
  webhook and graph runs cannot attach tasks to arbitrary chat or Task Flow
  sessions.

## [1.9.21] - 2026-06-18

### Changed

- Automation prompt overlays now tell agents how to create Task Flow work from
  webhook or cron runs: call `task.create` with the target `flow_id`, assign an
  employee owner, and leave session bindings to the runtime.

### Fixed

- Prompt-mode webhook and cron automations now resolve Task Flow mutations to the
  trusted `automation:<profile_id>:<automation_id>` principal, so strict public
  actor validation no longer rejects automation-created tasks with
  `task_creator_forbidden`.
- Release metadata and lockfile package metadata are aligned to `1.9.21`.

## [1.9.20] - 2026-06-18

### Added

- `afk update` now refreshes installed starter plugins such as `afkbotui` to the
  latest compatible release alongside the core runtime update, while non-starter
  plugins remain manually controlled through `afk plugin update`.
- Task Flow employees now receive stronger runtime guidance for inspecting
  project docs, using available tools, creating profile-local skills/subagents
  or employee descriptors when a role needs them, and respecting their explicit
  tool allowlist.
- Task Flow knowledge-maintenance sweeps now enforce a terminal-task cooldown per
  flow/source reference so autonomous CTO health checks do not create duplicate
  maintenance tasks after a recent completed or blocked sweep.

### Changed

- `afk setup`, `afk profile add`, and `afk profile update` now share the same
  quick/manual setup model. Quick setup enables practical file/network/tool
  access while keeping shell execution sandboxed by default.
- Channel add/update prompts now use shared wizard catalog copy so equivalent
  setup questions are phrased consistently across Telegram, Telethon, and
  Partyflow channels.
- Plugin install/update output now shows the full plugin URL and clearer
  authentication guidance, including Russian-localized setup text where the CLI
  already exposes Russian prompts.

### Security

- Employee session tool execution now applies the employee descriptor's
  `allowed_tools` as a hard runtime gate, including exact tool names,
  `prefix.*` groups, and the explicit `*` wildcard.
- Default seeded CTO descriptors are constrained to Task Flow, memory, read-only
  file, web, HTTP, and browser tools instead of broad subagent/runtime access.

### Fixed

- Secure credential resume now continues the interrupted LLM turn after
  `credentials.request` completes instead of stopping at the helper tool result.
- Task Flow operator-facing comment and document actions now consistently send a
  validated human actor identity through the plugin API path.
- Release metadata and lockfile package metadata are aligned to `1.9.20`.

## [1.9.19] - 2026-06-16

### Added

- Task Flow runtime now runs an autonomous CTO knowledge-maintenance sweep that
  creates or wakes one idempotent employee-owned task per unhealthy flow using
  `source_type=knowledge_maintenance`, `source_ref=flow:<flow_id>`, and
  `work_mode=knowledge_maintenance`.
- Knowledge-maintenance tasks now receive explicit runtime prompt guidance and a
  hard tool-policy gate limited to `task.*`, so CTO review work updates project
  docs, review queues, routing, and blockers instead of performing specialist
  implementation inside the maintenance task.

### Fixed

- Task Flow runtime maintenance throttling no longer treats disabled or
  owner-scoped knowledge maintenance as an always-due timer.
- Release metadata and lockfile package metadata are aligned to `1.9.19`.

## [1.9.18] - 2026-06-15

### Fixed

- Browser control now retries the Playwright import in long-running daemons after
  runtime installation, skips Playwright storage-state persist/load for
  Lightpanda CDP sessions, and treats CDP target conflicts as resettable browser
  failures instead of blocking successful actions.
- Release metadata and lockfile package metadata are aligned to `1.9.18`.

## [1.9.17] - 2026-06-15

### Changed

- Task Flow runtime, tools, docs, and tests now use employee-owned terminology
  for active work, feeds, delegation, and stale-claim recovery.
- `afk task create` now accepts only `--description` for Task Flow task
  instructions; the deprecated `--prompt` compatibility alias was removed.
- The Task Flow upgrade runner no longer converts old AI profile/subagent owner
  rows into employee tasks, leaving the employee-only Task Flow model as the
  single supported runtime path.
- Release metadata and lockfile package metadata are aligned to `1.9.17`.

## [1.9.16] - 2026-06-03

### Security

- Task Flow public task/flow delete operations now require an explicit validated
  actor identity and run the same principal/manager authorization checks as
  other durable mutations.
- Public employee actors now require live runtime session proof before they can
  mutate Task Flow state, preventing browser/API callers from spoofing an
  employee by sending only `actor_type=employee` and an employee ref.
- Public human/API callers can no longer bind arbitrary `session_id` or
  `session_profile_id` values to Task Flow tasks; session binding is reserved
  for live employee runtime sessions.

### Fixed

- Release metadata and lockfile package metadata are aligned to `1.9.16`.

## [1.9.15] - 2026-06-02

### Fixed

- Task Flow now turns structured employee blocker handoffs into idempotent
  manager escalation tasks for the blocked employee's active direct manager,
  wakes that manager, and annotates the blocked source task so autonomous
  recovery can continue without an operator manually creating follow-up work.
- Task Flow no longer treats root employees as global dispatchers for
  assignment changes; employee reassignment remains scoped to explicit reports,
  derived reports, and `can_delegate_to` targets.
- Task Flow employee prompts now describe manager escalation work as active
  management duty and tell blocked workers to emit structured handoff blocker
  codes when ownership policy prevents progress.
- Manager escalation source tasks now stay parked until a human operator or the
  responsible manager resolves them; the blocked employee cannot re-arm the
  source task by setting `ready_at` or moving it back to `todo`.
- Release metadata and lockfile package metadata are aligned to `1.9.15`.

## [1.9.14] - 2026-06-02

### Fixed

- `afk update` now treats the actual uv-tool receipt as the source of truth for
  GitHub/archive installs, including receipts stored as `git` or `url`
  requirements, so stale archive or package runtime metadata cannot replay an
  older install source.
- Release metadata and lockfile package metadata are aligned to `1.9.14`.

## [1.9.13] - 2026-06-02

### Fixed

- `afk update` now prefers a GitHub/archive uv-tool receipt over stale package
  runtime metadata, preventing a direct GitHub reinstall from being downgraded
  by an older saved package source on the next update.
- Release metadata and lockfile package metadata are aligned to `1.9.13`.

## [1.9.12] - 2026-06-02

### Fixed

- `afk update` now recovers the current uv-tool GitHub/archive source from
  `uv-receipt.toml` when runtime install-source metadata is missing, so
  GitHub release installs continue updating from the same source instead of
  falling back to the package channel.
- Package-source updates now refuse to downgrade an already newer AFKBOT
  install when the package channel lags behind the current runtime version.
- Release metadata and lockfile package metadata are aligned to `1.9.12`.

## [1.9.11] - 2026-06-02

### Fixed

- Task Flow now wakes the responsible employee manager when a blocked employee
  task explicitly needs manager reassignment or reports a `task_owner_forbidden`
  handoff, making stuck reassignment blockers visible in manager feeds without
  automatically changing task ownership.
- Release metadata and lockfile package metadata are aligned to `1.9.11`.

## [1.9.10] - 2026-06-01

### Added

- New runtime profiles now seed a default Task Flow `cto` employee descriptor in
  `profiles/<profile_id>/employees/cto.md`, giving every new organization a
  root employee for Task Flow ownership, delegation, and org-chart setup.

### Fixed

- Profile creation rolls back the default employee descriptor together with
  runtime config and bootstrap files if profile file seeding fails.

## [1.9.7] - 2026-05-27

### Added

- Task Flow services can now update editable flow metadata without changing the
  flow id or detaching existing tasks. Updates cover title, description, default
  owner, and labels, with the same actor and owner validation used by Task Flow
  operations.

## [1.9.6] - 2026-05-27

### Added

- Task Flow documents can now be hard-deleted through the service and document
  API with revision-conflict protection. Deletion removes the editable document
  and its immutable revision history, and records a task history event for
  task-scoped documents when the task still exists.

## [1.9.5] - 2026-05-27

### Fixed

- `afk task review-list --all-reviewers` now treats defaulted Click/Typer
  parameter sources consistently across installed runtime dependency versions,
  including environments where `ParameterSource.DEFAULT` enum instances differ.

## [1.9.4] - 2026-05-26

### Fixed

- `afk task review-list --all-reviewers` now works on installed runtimes where
  Click reports no parameter source for defaulted CLI options, instead of
  treating the default `--actor-type human` value as an explicit actor selector.

## [1.9.3] - 2026-05-26

### Added

- Added an all-reviewers Task Flow review queue view through
  `afk task review-list --all-reviewers` and `task.review.list` with
  `all_reviewers=true`, so orchestrators can find review work assigned to human,
  AI profile, or AI subagent reviewers.

### Changed

- Task Flow team prompts now require final deploy/runtime handoffs to re-check
  current service and worktree state, and to avoid production-looking `.env`
  files with placeholder secrets.
- Task Flow timing input handling for `task.update` and `task.block` is shared
  through one normalization helper.
- Release metadata and lockfile package metadata are aligned to `1.9.3`.

### Fixed

- `task.update` and `task.block` no longer reject LLM payloads that include both
  `ready_at=null` and `retry_after_sec=null`; null timing fields now clear or
  omit scheduling instead of causing `task_ready_at_conflict`.
- All-reviewer review queue listing now filters to actionable review work and
  does not leak normal claimed/running implementation tasks into review views.

## [1.9.1] - 2026-05-24

### Added

- Added OpenClaw-style plugin channel adapters so embedded plugins can register
  managed channel runtimes, inbound dispatchers, outbound `channel.send`
  delivery, setup metadata, and endpoint config schemas.
- Added AFKBOT bootstrap documentation for agents that need to create, install,
  test, disable, remove, or update self-authored channel integrations.

### Changed

- `afk channel plugin add` now validates plugin endpoint config schemas, applies
  adapter defaults, and can collect primitive schema fields interactively.
- Plugin scaffolds now generate a channel integration spec for provider-specific
  implementation, review, security, and test planning.

### Fixed

- Plugin channel adapters can no longer reserve or intercept core transports,
  and plugin disable/remove/update operations now guard existing channel
  endpoints that depend on installed adapters.

## [1.9.0] - 2026-05-24

### Added

- Added Task Flow AI-team prompt guidance with the profile AI as the single
  orchestrator, plus packaged starter subagents for architecture, backend,
  frontend, QA, review, docs, and DevOps workflows.
- Added strict default Task Flow team ownership so a profile backlog only accepts
  its own AI orchestrator unless teammate profile ids are configured.
- Added dedicated Task Flow team roster storage and an upgrade step that
  materializes existing cross-profile Task Flow participants into explicit
  rosters before strict enforcement.
- Release metadata and lockfile package metadata are aligned to `1.9.0`.

## [1.8.14] - 2026-05-21

### Removed

- Removed AFKBOT Cloud command and managed gateway implementation from the core
  `afkbotio` package. Cloud behavior now lives in the optional
  `afkbot-cloud-runtime` extension package.
- Removed `afk chat --cloud` and `afk start --cloud` aliases from core. Use
  `afk cloud chat` and `afk cloud start` from the Cloud runtime extension.

### Changed

- Core managed runtime startup now uses generic runtime extension hooks instead
  of Cloud-specific startup branches.
- Release metadata and lockfile package metadata are aligned to `1.8.14`.

## [1.8.13] - 2026-05-17

### Fixed

- `afk cloud chat` and `afk chat --cloud` now wait for the assistant reply
  instead of returning immediately after the user message is accepted.

## [1.8.12] - 2026-05-17

### Added

- Cloud-managed chat and task commands now forward AgentLoop progress events to
  the Cloud control plane, so dashboard chat can show tool/model activity while
  a turn is running.

## [1.8.11] - 2026-05-17

### Added

- Cloud-managed runtime startup now applies the Cloud manifest before launch,
  including profiles, channels, provider credentials, bootstrap files, skills,
  subagents, and webhook automations.
- Managed webhook automations can now reuse Cloud-issued webhook tokens so
  public Cloud webhook URLs route into the active runtime container.

### Fixed

- Cloud runtime command payloads and error logs now redact token-like and
  credential-like fields before sending diagnostics back to the control plane.
- `afk chat --cloud` and `afk start --cloud` continue to work without requiring
  local self-hosted setup on the operator machine.

## [1.8.10] - 2026-05-17

### Fixed

- Cloud runtime gateway now fails closed only before the first successful
  control-plane connection and reconnects after later gateway restarts, so API
  deploys do not stop already-running managed containers.

## [1.8.9] - 2026-05-16

### Fixed

- Cloud-managed `afk start` now applies idempotent persisted-state upgrades
  before connecting to Cloud, so hosted containers do not stop on canonical
  runtime config rewrites after an image update.

## [1.8.8] - 2026-05-15

### Added

- Added Task Flow flow/task documents with revision history, confirmation state,
  default flow docs, context bundles, AI mention feeds, and `task.doc.*`,
  `task.context.get`, and `task.feed.list` tools for autonomous agent work.
- Aligned AI agent feeds with runtime claim ownership so reviewer-assigned
  review tasks and active review claims appear in the same inbox that the
  detached scheduler uses.
- Fixed reviewer assignment updates so explicit null reviewer fields clear stale
  review routing while omitted reviewer fields continue to preserve existing
  assignments.

### Changed

- Release metadata and lockfile package metadata are aligned to `1.8.8`.

## [1.8.7] - 2026-05-15

### Changed

- Cloud-managed runtime startup now uses only the current
  `AFKBOT_DEPLOYMENT_MODE=managed` contract; the old managed-mode compatibility
  flag is removed.

## [1.8.5] - 2026-05-15

### Changed

- Cloud-managed runtimes now use workspace-local SQLite storage instead of an
  external PostgreSQL database contract.
- Managed `afk start` accepts the same SQLite-backed runtime state used by local
  AFKBOT, so bot containers can keep their state across image updates without
  direct database access to the Cloud control plane.

## [1.8.4] - 2026-05-14

### Fixed

- Fixed Telegram and PartyFlow channel runtime API calls so polling, replies,
  typing indicators, downloads, `channel.send` delivery, and PartyFlow
  `channel.history.list` keep working under locked-down profiles without
  exposing generic `app.run` to the agent.

## [1.8.3] - 2026-05-14

### Added

- Added remote Cloud command execution for saved bot connections:
  `afk cloud chat/start/stop/restart/setup`, `afk cloud profile add`, and
  `afk cloud channel add`.
- Added explicit aliases for common remote flows: `afk chat --cloud <name>
  --message ...` and `afk start --cloud <name>`.

### Changed

- PartyFlow channel ingress now uses Bot Event Polling (`/api/v1/bot/events`)
  instead of a public webhook receiver. Channel setup/status now expose polling
  cursor controls and no longer require a public Chat API URL or signing secret.

## [1.8.2] - 2026-05-13

### Fixed

- Fixed `mypy` failures in the Cloud remote connection service by normalizing
  optional Cloud API payload fields before persisting connection metadata.

## [1.8.1] - 2026-05-13

### Added

- Added `afk cloud connect` and `afk cloud list` so a local CLI can verify and
  save AFKBOT Cloud bot connections by public bot URL and scoped remote token.
  The CLI infers the Cloud API URL from workspace bot URLs by default while
  keeping `--api-url` for local development and private deployments.

### Security

- Cloud remote tokens are stored in the encrypted runtime secrets store and are
  never printed by `afk cloud connect --json` or `afk cloud list --json`.
- `afk cloud connect` rejects plain HTTP API URLs outside local development so
  scoped Cloud tokens are not sent to unsafe endpoints.
- Cloud runtime commands now fail explicitly on invalid `profile_id` values
  instead of falling back to the default profile.

## [1.8.0] - 2026-05-12

### Added

- Added AFKBOT Cloud runtime gateway support for managed containers, including
  outbound control-plane WebSocket auth, heartbeat, redacted event/log messages,
  fail-closed connectivity, chat command results, and cloud task update/result
  forwarding.

### Changed

- Managed runtime detection now uses only `AFKBOT_DEPLOYMENT_MODE=managed`, so
  Cloud startup has one supported runtime contract.
- Cloud gateway URL/token settings are env-only and are not loaded from persisted
  runtime config or runtime secrets.

### Security

- Managed cloud gateway URLs now require `wss://` by default. Local compose can
  opt into cleartext `ws://` only with `AFKBOT_CLOUD_GATEWAY_ALLOW_INSECURE_WS=1`.

## [1.7.4] - 2026-05-08

### Added

- Added runtime exposure guardrails for public binds and public runtime/chat API
  URLs. Exposed starts now require explicit auth-required posture, configured UI
  auth, and plugin API auth.
- Added managed PostgreSQL foundation for future Cloud Server launches:
  `database_per_bot` validation, `postgresql+asyncpg` dependency, bounded
  Postgres pool/timeouts, a bootstrap SQL contract with separate migrator/runtime
  roles, and a migration ledger contract.

### Changed

- Core plugin API routes now require operator auth when UI auth is configured,
  and operator-required plugin surfaces fail closed when auth is missing.
- Managed PostgreSQL runtime bootstrap validates pre-migrated schema and critical
  indexes instead of running DDL from the runtime role. Local SQLite bootstrap and
  legacy idempotent upgrades remain unchanged.
- Task Flow claim selection uses PostgreSQL `FOR UPDATE SKIP LOCKED` when running
  on Postgres while preserving the existing SQLite optimistic-claim behavior.
- Async CLI/service lifecycle paths now dispose fresh database engines for sync
  command invocations instead of leaking async resources across event loops.
- File, browser, channel, profile, skill, subagent, and automation runtime paths
  now offload filesystem work from async hot paths to worker threads.

### Security

- PartyFlow webhook signing can now be made mandatory with
  `AFKBOT_PARTYFLOW_WEBHOOK_SIGNING_REQUIRED=1`; the default remains compatible
  with existing local/self-hosted installs that intentionally left the secret
  blank.

## [1.7.3] - 2026-05-04

### Changed

- Moonshot/Kimi API key errors now report the provider rejection, HTTP status,
  configured provider/base URL, and provider response detail without suggesting
  alternate providers.

## [1.7.2] - 2026-05-03

### Added

- Added a separate `moonshot-cn` provider for the Kimi China API endpoint
  (`https://api.moonshot.cn/v1`) with its own base URL and API key settings.
- Refreshed setup model presets for OpenRouter, OpenAI, Claude, DeepSeek, xAI,
  Qwen, MiniMax, and GitHub Copilot based on current provider catalogs.

### Fixed

- `afk browser install` now uses `uv pip install --python ...` when uv is
  available, avoiding `pip` module failures in isolated uv-tool environments.
- Moonshot/Kimi authentication failures now surface as credential errors with
  localized setup/profile messages and guidance for direct Kimi keys versus
  OpenRouter keys.
- Release metadata and lockfile package metadata are aligned to `1.7.2`.

## [1.7.1] - 2026-05-03

### Added

- Setup/profile security configuration now has an intent-first guided wizard
  with Russian/English copy for work surfaces, allowed actions, isolation,
  confirmations, and network boundaries. The new wizard stores additive V2
  metadata while keeping legacy scenario ids as compatibility-only labels.
- OpenAI Codex profile setup now supports file-backed OAuth tokens: AFKBOT can
  store the local Codex auth file path and reread the latest access token at
  runtime instead of copying the token into profile secrets.

### Changed

- Recommended setup now uses a quick-safe default for chats/channels, tasks, and
  memory without granting file, shell, credentials, browser, app, MCP, or
  subagent tools until the operator explicitly chooses them.
- Channel wizard labels now avoid raw internal tokens in normal prompts for
  access/session/tool-profile choices.

### Fixed

- Release metadata and lockfile package metadata are aligned to `1.7.1`.

## [1.7.0] - 2026-05-02

### Added

- Setup/profile/channel wizards now have a shared tested scenario catalog and
  renderer-neutral question inventory, including profile security scenarios,
  Telegram/Telethon/PartyFlow channel scenarios, and localized preview text.
- Channel-owned tools now include current-channel `channel.send` defaults for
  Telegram, Telethon, and PartyFlow turns without exposing generic `app.run`,
  shell, or filesystem tools.
- `afk sandbox status` reports the active OS sandbox backend so operators can
  verify shell isolation before enabling shell-capable profiles.

### Changed

- Interactive channel setup can start from high-level safe defaults while keeping
  explicit CLI flags and persisted endpoint values as the compatibility source of
  truth.
- Setup state migration now additively records wizard metadata and workspace
  scope so older installs can be upgraded without manual config edits.
- Legacy restricted-shell profile policies are upgraded to fail closed with a
  required OS shell sandbox, including older empty allowed-directory snapshots.

### Fixed

- PartyFlow operator readiness probes no longer require opening generic
  `app.run` to safe channel profiles; the CLI probe validates bot credentials
  directly while channel AI permissions remain scoped.
- Release metadata and lockfile package metadata are aligned to `1.7.0`.

## [1.6.2] - 2026-05-01

### Changed

- PartyFlow channel setup now prints a local Chat API webhook URL when no usable
  public `AFKBOT_PUBLIC_CHAT_API_URL` is configured, while still preferring a
  valid public HTTPS base URL for real PartyFlow deliveries. Status output marks
  local-only URLs as not public-delivery-ready to avoid false rollout readiness.

### Fixed

- Release metadata and lockfile package metadata are aligned to `1.6.2`.

## [1.6.1] - 2026-04-30

### Changed

- PartyFlow channel setup now treats the webhook signing secret as optional and
  explains that leaving it blank disables webhook signature validation.

### Fixed

- PartyFlow webhook runtime, status, and probe flows no longer fail when only the
  optional webhook signing secret is absent; configured signing secrets still
  enforce the existing timestamped HMAC validation.
- Release metadata and lockfile package metadata are aligned to `1.6.1`.

## [1.6.0] - 2026-04-30

### Added

- PartyFlow webhook channels are now available through `afk channel partyflow`,
  with bilingual setup/update/show flows, copyable webhook URLs, readiness
  probes, trigger modes, batching, and private/group/outbound access controls.
- PartyFlow Bot REST operations are exposed through `app.run`, including bot
  identity lookup, conversation join, message send, and channel history reads.
- Agent loops can now send plain-text outbound PartyFlow channel messages
  through `channel.send`, with outbound allowlist enforcement and long-message
  splitting.

### Changed

- Channel access-policy binding generation now supports PartyFlow-specific
  sender and conversation rules while preserving the shared Telegram channel
  policy model.

### Fixed

- PartyFlow webhook ingress now verifies raw-body signatures, requires delivery
  identifiers for idempotency, ignores unsupported update events, maps disabled
  endpoints to non-retry responses, and applies channel policy to webhook
  payloads that omit `conversation_type`.
- Release metadata and lockfile package metadata are aligned to `1.6.0`.

## [1.5.4] - 2026-04-29

### Added

- Telegram Bot polling channels now download inbound media attachments into the
  profile workspace, including voice, audio, documents, photos, videos,
  animations, video notes, and stickers.
- `channel.send` now supports rich Telegram payloads with parse modes, web
  preview control, reply/inline keyboards, media attachments, and private-chat
  draft previews.
- Telethon userbot channels now expose inbound media paths to agent turns and
  support rich outbound file sends through workspace-scoped attachments.

### Fixed

- Telegram forum-topic callback queries now preserve `message_thread_id` for
  topic-aware routing.
- Telegram Bot and Telethon media paths and file sizes now fail closed for
  outside-scope or oversized local media.
- Animated and video Telegram stickers now keep `.tgs` and `.webm` download
  extensions instead of being forced to `.webp`.
- Release metadata and lockfile package metadata are aligned to `1.5.4`.

## [1.5.3] - 2026-04-28

### Changed

- Task Flow owner inputs now accept the public `subagent` owner type alias while persisting canonical `ai_subagent` ownership for runtime scheduling.
- Task Flow review inbox, approve, and request-changes paths now normalize the same `subagent` actor alias before actor validation and tool spoof checks.
- Release metadata and lockfile package metadata are aligned to `1.5.3`.

## [1.5.2] - 2026-04-28

### Fixed

- Task Flow runtime schema upkeep now migrates legacy `task.prompt` data into canonical `task.description` rows before hot-path runtime indexes are applied.
- Legacy task migrations now preserve an existing non-empty `description` when both `description` and `prompt` columns are present, falling back to `prompt` only for blank descriptions.
- Release metadata and lockfile package metadata are aligned to `1.5.2`.

## [1.5.1] - 2026-04-28

### Changed

- Channel, MCP, browser, setup, provider, and plugin wizards now use clearer bilingual copy with beginner-friendly option descriptions while still saving stable raw config values.
- Telegram Bot API and Telethon channel setup now explains private 1:1 bots, group allowlists, routing bindings, session grouping, and safe channel tool-profile choices more directly.
- The interactive channel wizard now surfaces outbound `channel.send` target restrictions for profiles that expose outbound channel messaging.

### Fixed

- Telethon reply mode `disabled` is now described as read-only/no-reply mode instead of being confused with access-policy `disabled`.
- Package update checks no longer offer an update from the current version to the same version when a stale saved installer target is present.
- Release metadata and lockfile package metadata are aligned to `1.5.1`.

## [1.5.0] - 2026-04-27

### Added

- Telegram Bot API and Telethon channel wizards now include bilingual private-chat, group-chat, sender allowlist, and outbound target allowlist settings.
- `channel.send` is available as a profile-scoped tool for explicit outbound channel replies, with channel-level outbound allowlist enforcement.
- Inbound Telegram media summaries now describe richer attachments such as stickers, GIF/animation, video, audio, voice, photo, and document payloads for the model.

### Changed

- Channel matching bindings can now be generated from access policies, producing scoped direct-chat and group/user binding rules instead of only a broad endpoint binding.
- Safe channel tool profiles now allow `channel.send` while still blocking broad `app.run` access on user-facing channels.
- Release metadata and lockfile package metadata are aligned to `1.5.0`.

## [1.4.7] - 2026-04-27

### Added

- Persistent bounded diagnostic error logs under the runtime `logs/` directory, with `afk logs` commands for locating, listing, tailing, and cleaning log files.
- README preview artwork sourced from the AFKBOT web project.

### Changed

- License explainer docs are slimmer: `LICENSE_FAQ.md` is the single human-readable commercial-use summary, and the duplicate `COMMERCIAL_LICENSE.md` file was removed.
- Local-only `docs/`, `plans/`, and root `AGENTS.md` / `agents.md` files are ignored by git.

### Fixed

- Unhandled API, CLI, runtime, and tool execution exceptions now write redacted traceback context to operator-readable files instead of only surfacing as generic 500/tool failures.
- Code of Conduct reporting no longer points conduct reports at the security vulnerability mailbox.
- Release metadata, runtime version surfaces, and lockfile package metadata are aligned to `1.4.7`.

## [1.4.6] - 2026-04-23

### Changed

- Chat progress output now reports clearer model lifecycle states such as queued, started, running, and timed out instead of repeating generic `thinking...` lines for low-level LLM events.
- Repository-local `docs/` content has been removed from the source tree, and bundled references were cleaned up so checkout layout stays focused on runtime code and tests.

### Fixed

- Parallel tool progress/result rows now preserve stable call grouping by provider `call_id`, preventing unrelated tool results from collapsing under the same progress marker.
- Approval/profile-resume flows now preserve the original tool `call_id`, so resumed tool execution keeps the same progress correlation and transcript linkage as the original call.
- Release metadata, runtime version surfaces, and lockfile package metadata are aligned to `1.4.6`.

## [1.4.5] - 2026-04-22

### Changed

- `afk chat` now treats unnamed interactive launches as fresh sessions by default, shows the active session id in the chat UI, and keeps explicit named sessions reusable without silently reusing the last anonymous transcript.
- Interactive chat runtime calls now tolerate adjacent-version REPL/runtime signature drift during upgrades, reducing mixed-binary failures while shells, prompt sessions, or installed tool entrypoints are temporarily out of sync.

### Fixed

- Explicit Task Flow task creation now converts legacy `task` / `task_event` schema mismatches into a structured compatibility error with upgrade guidance instead of bubbling a generic storage-backed 500.
- `afk chat` rejects opening the same explicit session from multiple terminals at once, while still allowing independent anonymous sessions to run in parallel.
- Release metadata, runtime version surfaces, and lockfile package metadata are aligned to `1.4.5`.

## [1.4.2] - 2026-04-21

### Changed

- Webhook automations now persist an encrypted-at-rest secret copy for operator-side endpoint reveal while keeping generic automation metadata, CLI reads, tool reads, and LLM-facing list/get surfaces masked by default.
- AFKBOT UI can rehydrate the current webhook URL inside the automation inspector through an operator-only reveal path instead of browser or plugin-process secret caches.

### Fixed

- Legacy plaintext webhook rows now fail closed during schema upgrade until `AFKBOT_CREDENTIALS_MASTER_KEYS` is configured, preventing silent secret loss or indefinite plaintext persistence during rollout.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.4.2`.

## [1.4.1] - 2026-04-20

### Fixed

- `afk auth setup` and related interactive UI-auth flows now accept both keyword and legacy positional prompt calls, avoiding a `TypeError` during username prompting on affected installed binaries.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.4.1`.

## [1.4.0] - 2026-04-20

### Added

- Graph-mode automations with persisted DAG flows, branching/fan-out execution, terminal inspection commands, and secure code-node execution alongside the existing prompt-mode path.
- AI, subagent, task, and action node adapters plus graph trace/read-model coverage so automations can move between deterministic processing and LLM fallback without conflicting runtimes.
- A new Memory V2 architecture with pinned `profile_memory_item` storage, explicit conversation recall, and a dedicated consolidation layer between extraction, archival memory, and core memory.
- Regression coverage for graph runtime schema upgrades, sandbox enforcement, runtime import smoke, local-first memory fallback, recall authorization boundaries, core-memory rendering, and profile-memory persistence/index creation.

### Changed

- Automations now support both `prompt` and `graph` execution modes with profile-aware tool/runtime settings, safe fallback rules, and automation principals for Task Flow mutations.
- Agent loop preparation now injects trusted core memory as a separate prompt block, while automatic search/save paths route through the new memory consolidation policy instead of mixing storage decisions into extraction.
- Scoped memory fallback is now owned by the memory service, and conversation recall is opt-in through runtime settings rather than enabled by default.

### Fixed

- Graph runtime startup/import cycles, profile runtime version resolution, and sandbox fallback handling are now aligned so release/CLI startup paths stay stable under CI and tagged builds.
- Mixed temporary and durable user statements no longer drop the durable fact when both appear in one sentence or clause.
- Profile deletion now cleans up the new core-memory tier, and release/runtime config surfaces stay aligned with the Memory V2 settings.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.4.0`.

## [1.3.0] - 2026-04-19

### Added

- Platform-level browser UI auth for plugin web surfaces and plugin API routes, including login, logout, session inspection, signed operator cookies, rate limiting, and lockout behavior.
- New `afk auth ...` command surface with guided setup, create, update, status, password rotation, and disable flows.
- Plugin manifests can now declare `auth.operator_required`, so protected browser surfaces survive plugin upgrades without storing auth state inside plugin packages.

### Changed

- Plugin web auth enforcement now runs in core `afk` instead of relying on per-plugin logic, and protected UI routes preserve the original `next=` target including query parameters.
- The curated AFKBOT UI plugin can now consume core auth directly and show operator session state without maintaining its own password flow.

### Fixed

- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.3.0`.

## [1.2.0] - 2026-04-16

### Added

- Task Flow now supports persisted task attachments end-to-end, including runtime delivery of attached context to AI-owned executions.
- Task Flow boards and APIs now expose a leading human-only `PLAN` lane so operators can stage work before it becomes claimable by AI workers.
- Release validation now includes stricter migration and runtime coverage for the Task Flow `description`/attachments rollout.

### Changed

- Task Flow has migrated from `prompt` to `description` as the canonical task body across the service, CLI, tools, and release smoke coverage.
- Plan-only chat turns keep the normal runtime iteration budget instead of being artificially clamped to two iterations, while still remaining read-only.

### Fixed

- Legacy SQLite Task Flow installs are rebuilt safely so old `task.prompt` data lands in `description` and fresh inserts no longer fail after upgrade.
- Task Flow runtime and operator surfaces now stay aligned when attachments, plan-stage tasks, and detached execution handoffs are involved.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.2.0`.

## [1.1.1] - 2026-04-15

### Added

- `afk service host <host>` now lets operators persist the managed runtime bind host without coupling that change to a port rewrite.

### Changed

- Managed runtime bind persistence now routes host-only and port changes through the same reload and rollback path.

### Fixed

- Switching the managed AFKBOT service from `127.0.0.1` to `0.0.0.0` on the same runtime port pair no longer requires a manual stop before reload.
- `afk task create` keeps backward compatibility by accepting legacy `--prompt` as a deprecated alias for `--description`, with deterministic precedence (`--description` wins when both are present) and default status preserved as `todo`.
- Task attachment ingestion now enforces a pre-decode base64 payload size guard before `base64.b64decode(..., validate=True)` and still keeps the post-decode byte-size limit as a second safety layer.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.1.1`.

## [1.1.0] - 2026-04-15

### Added

- Parallel planning strategy guidance for chat/runtime operator flows, making multi-tool execution planning more explicit.
- Profile-aware AI employee Task Flow execution guards, plus scheduler fairness documentation and regression coverage for the new guarded runtime paths.

### Changed

- Task Flow runtime session binding, principal propagation, ownership handoff, and profile-scope resolution are now aligned across `task.create` and detached runtime execution flows.
- Plugin CLI/operator output and companion docs are clearer around installed plugin surfaces and day-to-day operator usage.

### Fixed

- Cron automations now honor configured IANA timezones when calculating `next_run_at`, and legacy invalid timezone rows fail only their own job instead of aborting the whole cron tick.
- Managed runtime startup now fails closed with stronger diagnostics and consistently routes managed services through the Python entrypoint.
- Setup/runtime hardening now verifies OpenAI Codex auth earlier, safely rejects Codex verification rate-limit failures, retries transient LLM provider errors, and keeps the chat secret guard opt-in.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.1.0`.

## [1.0.13] - 2026-04-14

### Added

- Managed service lifecycle commands under `afk service ...` for install, start, stop, restart, status, and persisted runtime port updates.
- Cross-platform daemon diagnostics in `afk doctor` and `afk service status`, including live AFKBOT health checks instead of service-manager-only signals.
- Regression coverage for Linux systemd system-level installs, user-level fallback behavior, service uninstall cleanup, and environment-isolated `afk start` CLI flows.

### Changed

- Linux managed runtime installs now prefer a system-level `systemd` unit when that path is available, while falling back to an enabled user unit with explicit `loginctl enable-linger` guidance when root-level installation is unavailable.
- Managed `systemd` and `launchd` service actions now wait for real AFKBOT health before reporting success, and managed service definitions no longer autostart from bootstrap-only state unless setup is fully completed.
- `install.sh`, `afk update`, and managed runtime reload flows now refresh the installed service definition instead of assuming a previously provisioned daemon can be reused unchanged.

### Fixed

- Repeated install/update flows now re-evaluate managed daemon startup instead of leaving stale Linux/macOS service definitions behind.
- Persisted runtime port changes roll back cleanly when a managed reload cannot come back healthy.
- Linux uninstall now removes managed system-level AFKBOT units instead of leaving reboot autostart artifacts behind.
- Release metadata, API versioning, README install examples, and update-runtime expectations are aligned to `1.0.13`.

## [1.0.12] - 2026-04-11

### Added

- README now documents the runtime execution model directly in the repository root, including planning modes, per-session queueing, the `chat` vs `session.job.run` vs `Task Flow` decision model, and copy-paste chat examples for parallel work.
- Regression coverage for runtime subagent name normalization, cross-instance subagent cancellation, and subagent-specific validation errors surfaced through `subagent.run` and `session.job.run`.

### Changed

- Chat, API, automations, Task Flow, and child subagents now share the same session-orchestration model: one serialized turn queue per `(profile_id, session_id)` with parallel fan-out only inside the active turn.
- Planning-first chat now runs `plan -> execute` inside the same serialized session slot and no longer requires a second user message to continue after a visible plan pass.
- Runtime subagent lookup now normalizes requested names the same way profile subagent creation does, so case and localized input resolve to the same runtime-safe slug when a matching subagent exists.
- API idempotent turn execution no longer keeps the legacy optional-shape shim around the turn executor call contract.

### Fixed

- Detached subagent cancellation is now durable across service instances: when another runtime instance marks a subagent task cancelled, the active worker observes the persisted state, forwards cancellation into the child session runtime, and avoids orphan child turns continuing after visible cancellation.
- `session.job.run` now respects nested capability boundaries: shell-only profiles cannot spawn batch subagents, and subagent-only profiles cannot execute batch shell commands through the shared wrapper tool.
- `subagent.run` and `session.job.run` now return `invalid_subagent_name` for invalid runtime names instead of collapsing those failures into a generic parameter error.
- Missing subagent lookup errors now include the visible runtime subagent names for the current profile, making it easier to distinguish “bad name” from “valid name but not installed in this AFK profile”.
- Release metadata, API versioning, README examples, and update-runtime expectations are aligned to `1.0.12`.
- Publish workflow now enforces `vX.Y.Z == project.version` on tag releases and runs `twine check` before uploading artifacts.

## [1.0.11] - 2026-04-09

### Fixed

- Fresh uv-tool installs now let `afk update` create the runtime root on demand before replaying bootstrap-only setup, so updates no longer fail with missing `AFKBOT` application-support directories.
- Legacy uv-tool installs without saved installer metadata now default to the published `afkbotio` PyPI package for update checks and reinstalls instead of falling back to the GitHub archive path.
- Installer-style `afk update` now skips the post-update `doctor` pass until full `afk setup` has completed, allowing newly installed tools to self-update successfully before initial setup.

## [1.0.10] - 2026-04-09

### Fixed

- `afk update` for uv-tool installs now replays the canonical hosted GitHub archive source when installer metadata is missing, instead of trying to resolve an unavailable `afkbotio` registry package.
- Legacy uv-tool update notices now fall back to the same hosted archive source, keeping update checks aligned with the install scripts.

## [1.0.9] - 2026-04-09

### Fixed

- Fresh installs from `install.sh` now declare `packaging` as an explicit runtime dependency, preventing `afk` startup failures when `afkbot.services.update_runtime` imports `packaging.version`.
- Release metadata and install examples now point to `1.0.9`, matching the hotfix build shipped from `main`.

## [1.0.8] - 2026-04-08

### Added

- Curated plugin catalog and operator docs now point to the `AFKBOT UI` companion plugin (`afkbotui`) instead of the earlier kanban-only example.
- Legacy SQLite automation installs now receive idempotent schema upgrades for delivery metadata columns during bootstrap.
- Regression coverage for localized update summaries, uv-backed editable refresh, and legacy automation schema upgrades.

### Changed

- `afk chat` startup update notices now finish with localized success summaries, keeping Russian and English flows consistent end-to-end.
- Host and managed editable update paths now refresh the environment through `uv pip --python ... --editable ...`, matching the uv-based install model.
- Full-access `afk chat` sessions now start from the operator's current shell directory instead of always falling back to the profile workspace root.
- README plugin guidance now treats `AFKBOT UI` as the current extensible web workspace for automations today and future Task Flow/operator surfaces.

### Fixed

- Existing SQLite installs no longer fail ORM reads after automation delivery fields were added to the `automation` model.
- Chat-time self-update no longer breaks in uv-managed environments that do not ship `pip` inside the active interpreter.
- `openai-codex` SSE decoding now rehydrates assistant output from `response.output_item.done`, fixing provider responses that previously surfaced as temporary provider failures.

## [1.0.7] - 2026-04-07

### Added

- Embedded plugin runtime with manifest-based install, enable, disable, update, inspect, config, and scaffold flows under `afk plugin ...`.
- Plugin extension surfaces for API routers, static web apps, tools, skills, apps, and optional lifecycle hooks.
- Plugin discovery/config API endpoints for installed plugins at `/v1/plugins...`.
- First external plugin path for `Task Flow` via the companion Kanban web plugin repository.
- Stronger Task Flow operator surfaces for AI comment discipline and richer runtime handoff behavior when work is routed through plugin-driven UI.

### Changed

- API app startup now loads enabled plugins and mounts their routes and static assets during the main FastAPI lifespan.
- Runtime/plugin compatibility is now version-gated through plugin manifest `afkbot_version` constraints.
- Local plugin runtime state is treated as generated machine state rather than tracked repository content.

### Fixed

- Plugin config patching now merges over defaults instead of accidentally requiring full replacement payloads.
- GitHub archive plugin installs now clean up temporary extraction state correctly.
- Task Flow background runs now emit fallback durable comments when execution reaches meaningful terminal states without an explicit operator note.

## [1.0.6] - 2026-04-06

### Added

- Full `Task Flow` domain with durable `task_flow`, `task`, `task_dependency`, `task_run`, and `task_event` persistence.
- Detached `taskflow` runtime for AI-owned backlog execution, including dependency unblocking, review handoff, and stale-lease recovery.
- CLI and tool surfaces for board/inbox/review/run history/event history/comments/stale-claim repair across `afk task ...` and `task.*`.
- Human startup digest for Task Flow work at `afk chat` start, including reviewer-routed items and inbox dedupe cursors.
- Release verification artifacts for Task Flow: a deterministic smoke script and a manual release checklist.
- OAuth-ready LLM provider catalog entries for `openai-codex` (ChatGPT OAuth), `minimax-portal` (device-code OAuth), and `github-copilot` (GitHub device flow).
- Setup/profile credential flows for OAuth providers in `afk setup`, `afk profile add`, and `afk profile update`, including Codex token import from local CLI auth state.

### Changed

- `afk start` now launches the dedicated Task Flow runtime alongside existing automation runtime services.
- Background Task Flow execution now uses its own `transport="taskflow"` prompt overlay and runtime context.
- Operator maintenance now exposes explicit stale-claim inspection and repair flows instead of relying only on automatic runtime sweep.
- OpenAI-compatible provider runtime now supports Codex Responses SSE decoding, MiniMax OAuth refresh persistence, and GitHub Copilot token exchange for provider requests.
- Provider/base-url profile resolution now keeps provider defaults aligned when switching providers without an explicit custom base URL.

### Fixed

- Human inbox unseen counts are now lossless even when relevant events are buried behind newer irrelevant runtime noise.
- Human inbox unread summary no longer materializes the full unseen event tail in Python; count and preview queries now stay bounded at the repository layer.
- Notification cursor writes are atomic and trusted-only for `mark_seen` flows.
- Expired Task Flow claims are repaired safely without clobbering refreshed live leases.
- Codex stateless tool-followup requests no longer fail on replayed `reasoning` item ids when `store=false`; follow-up `/responses` calls now complete reliably.
- Provider fallback error handling now truncates surfaced upstream details and maps Codex replay lookup 404 failures to invalid-request instead of model-not-found.

## [1.0.5] - 2026-04-04

### Added

- Path-based automation webhook URLs using `/v1/automations/<profile_id>/webhook/<token>` plus richer webhook metadata in `afk automation get/list`.
- Webhook execution tracking fields including status, timestamps, last session id, event hash, and a chat resume command for inspecting the last automation session.
- MCP profile-management flows for both operators and agents: `afk mcp connect/get/validate`, `mcp.profile.*` tools, and the built-in `mcp-manager` skill.
- Installer/setup guidance updates for locale-aware first run flows and MCP onboarding in the README.

### Changed

- `afk update` now replays the saved installer source so updates follow the same source-selection logic as `install.sh` and `install.ps1`.
- Fresh installs now auto-select and persist a non-default local runtime port pair; `afk doctor` shows the effective runtime/chat ports and saved prompt language.
- `afk setup` now auto-detects the system locale, persists `--lang`, and skips unnecessary base-URL prompts for standard providers.
- Installer and setup success messaging now points users directly to `afk setup`, `afk doctor`, and `afk chat` instead of requiring manual command discovery.

### Fixed

- CLI and runtime webhook flows now expose stable, usable URLs instead of header-token-only webhook wiring.
- MCP management tools remain gated behind the dedicated skill boundary instead of leaking into the normal tool surface.
- Runtime port resolution helpers are now explicitly typed so the strict `mypy` quality job stays green for the new port-selection flow.

## [1.0.4] - 2026-04-03

### Added

- Automatic context compaction recovery when a provider rejects a request because the model context window was exceeded.
- Visible progress markers during recovery so fullscreen and CLI sessions show when compaction starts and when the context has been compacted.
- Regression coverage for overflow classification, compaction retry flow, and compaction progress rendering.

### Changed

- Request compaction now uses a hybrid strategy: LLM-generated handoff summaries first, deterministic fallback second.
- Session compaction and in-iteration retry flow now preserve the core prompt while replacing older carryover history with compact summaries.

### Fixed

- Agent-loop executions now recover from context-window overflow errors instead of immediately failing the run when compaction can reduce the payload.
- Provider error handling now classifies context-window overflow separately from generic invalid-request failures.

## [1.0.3] - 2026-04-02

### Added

- Expanded setup/provider catalog with first-class `claude` and `moonshot` options, including provider-specific defaults and API key/base URL wiring.
- Refreshed OpenRouter setup presets to the current top-20 model list while keeping manual model entry available.
- Improved fullscreen tool progress UX with clearer timeline states, status markers, and compact rolling output previews.

### Changed

- Unified automation ingress flow through the agent-loop execution path to reduce duplicated runtime entrypoints.
- Refined fullscreen secure approval/chat interaction prompts for more consistent in-session behavior.
- Improved long-running progress readability with clearer elapsed-time rendering and lower-noise progress updates.

### Fixed

- Fixed policy network host extraction crash on malformed shell tokens that previously surfaced as `ValueError: Invalid IPv6 URL`.
- Fixed one-time tool approval and selection stability edge cases in fullscreen chat prompts.
- Fixed typing issues in fullscreen prompt callbacks to keep CI static checks green.

## [1.0.2] - 2026-03-31

### Added

- New `afk version` command for quickly verifying the active local checkout, package version, and git revision during manual testing.
- Regression coverage for fullscreen transcript tail rendering, setup-guard access to `afk version`, and local checkout version resolution.

### Changed

- Fullscreen chat transcript now renders only the newest visible lines in docked mode instead of relying on an internal scrollable transcript pane.
- Fullscreen chat startup now clears terminal scrollback before handing off to the alternate-screen workspace, reducing false right-side scrollbar carry-over from the shell host.
- API application version metadata now matches the packaged release version `1.0.2`.

## [1.0.1] - 2026-03-30

### Added

- `uv` tool-based hosted install/update/uninstall flow documentation and advanced command examples.
- Regression coverage for uv-tool installs, runtime/app path resolution, and safer installer migration behavior.

### Changed

- Hosted installers on macOS, Linux, and Windows now install AFKBOT through `uv tool install` instead of the previous managed snapshot/virtualenv flow.
- Hosted installers now resolve GitHub sources through source archives instead of `git+...`, so default installs no longer require a system Git executable.
- `afk update` now detects uv-tool installs and upgrades them through `uv tool upgrade afkbotio --reinstall`.
- Installed-tool runtime state now lives in user-local data directories while bundled bootstrap, skills, and subagent assets continue to resolve from the packaged app.
- Unix installers now use `--reinstall`, defer legacy PATH cleanup until the new install/bootstrap succeeds, and keep legacy wiring intact when bootstrap fails.
- Unix uninstall now tolerates missing uv-tool state and continues cleaning legacy PATH blocks, symlinks, and install roots.
- Windows PowerShell installer and uninstaller now fail correctly on non-zero native command exits instead of printing false success.

### Removed

- Hosted installer support for the managed `--install-dir` workflow.

## [1.0.0] - 2026-03-25

### Added

- Initial public source-available release of AFKBOT.
- Public project metadata and contribution policy files.
- Simplified root README focused on installation and project overview.
- Fair-code/source-available licensing, contributor agreement, and trademark policy files.

### Changed

- Version updated to `1.0.0`.
- Repository cleaned for public distribution by removing internal planning and documentation layers.
- Manual local source startup and setup flow now runs on local SQLite only.
- `afk setup`, `afk update`, and `afk uninstall` now target the local source/runtime flow directly.
- Managed install scripts now stage self-hosted source snapshots, install Python 3.12 through `uv`, and keep runtime state outside the app source tree.
- Semantic memory now persists embeddings directly in SQLite-backed storage.
- New installs now create a clean SQLite schema directly instead of carrying legacy schema patch chains.
- Repository licensing switched from MIT to the `Sustainable Use License 1.0`.

### Removed

- Internal-only docs, agent guidance, manual reports, and service README notes that were not part of the public product surface.
- Tracked container-runtime files and legacy local source flow requirements.
- Legacy `scripts/update.sh` and `scripts/release.sh` wrappers.
