# Changelog

All notable changes to this project will be documented in this file.

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

- Hosted installer support for the legacy managed `--install-dir` workflow.

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
