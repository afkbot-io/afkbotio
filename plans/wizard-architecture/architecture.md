# Architecture

## Current Boundary

Prompt questions are distributed across `setup_policy_prompts.py`, `setup_provider_prompts.py`, `profile_resolution.py`, `channel_shared.py`, and transport-specific channel files. Runtime behavior is mostly correct, but question order, labels, branch conditions, and preview text are not represented as one testable model.

## Target Boundary

- `afkbot.services.wizard.contracts`: renderer-neutral contracts.
- `afkbot.services.wizard.profile_catalog`: profile/security scenario templates and setup/profile inventory.
- `afkbot.services.wizard.channel_catalog`: channel scenario templates and channel inventory.
- `afkbot.services.wizard.preview`: reusable preview builders.
- CLI prompt helpers remain renderer adapters and consume shared labels/defaults where practical.

## Compatibility Strategy

- Persisted values stay the same: `policy_preset`, `policy_capabilities`, `policy_file_access_mode`, `policy_workspace_scope`, `tool_profile`, `session_policy`, transport-specific endpoint configs.
- New wizard metadata is additive.
- Old configs that do not include wizard metadata infer scenario as `custom`.
- Upgrade runner is idempotent and rewrites only canonical setup state/runtime snapshots.
- Non-interactive flags remain the source of truth when provided.

## Alternatives Considered

- Copy-only UX cleanup: cheaper, but would keep branches duplicated.
- Full TUI rewrite: better long term, too risky for this release.
- DB schema change for wizard answers: unnecessary now; inferred additive metadata is enough.

## Rollback

- If wizard metadata causes issues, commands can ignore it because runtime persists canonical old fields.
- Upgrade is additive and can be rerun safely.
