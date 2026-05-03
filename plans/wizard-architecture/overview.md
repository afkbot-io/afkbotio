# Wizard Architecture Variant B

## Goal

Introduce a shared wizard planning layer for setup, profile, and channel flows so questions, branches, previews, and compatibility rules are explicit and testable.

## Scope

- Add shared `WizardPlan` contracts and catalogs for profile/security/channel scenarios.
- Keep existing Typer commands and non-interactive flags compatible.
- Add scenario-oriented previews for profile and channel setup.
- Add setup-state compatibility migration for the new wizard metadata.
- Add tests for question inventory, scenario defaults, previews, and migration.

## Non-Goals

- Replacing the CLI renderer with a new TUI.
- Removing existing raw config values or CLI flags.
- Reworking provider auth, Telegram runtime, Telethon runtime, or PartyFlow webhook delivery internals.

## Acceptance Criteria

- Existing CLI flags continue to work.
- Existing persisted setup/profile/channel configs load without manual edits.
- Wizard question inventory is generated from shared definitions and covered by tests.
- Scenario presets produce stable profile/channel defaults.
- Final preview can explain profile ceiling, channel surface, credentials, filesystem scope, shell mode, network, and warnings.
- Setup-state migration is idempotent and tested.
- Full test suite, ruff, mypy, and targeted live-style CLI smoke tests pass.

## Current Status

- Implemented and reviewed.
- Current code has shared wizard plan contracts, profile/channel scenario catalogs, setup-state wizard metadata migration, and snapshot/inventory tests.
- Existing Typer commands and raw flags remain the compatibility boundary; scenario choices only provide defaults in real interactive terminals.
- Live interactive setup/profile/PartyFlow/Telegram smoke tests passed in English and Russian, including sandbox-shell setup, Task Flow channel profile, PartyFlow webhook keywords, and Telegram group mention scenarios.
- Final verification passed for release metadata `1.7.0`.

## Risks

- Accidentally changing defaults for existing users.
- Duplicating scenario logic in setup/profile/channel commands.
- Making channel tools too broad and exposing cross-channel data.
- Treating `best_effort` shell sandbox as hard isolation in UX copy.

## Finish Checklist

- [x] Plan files created.
- [x] Orchestrator review collected.
- [x] Tests added before production behavior changes.
- [x] Wizard contracts and catalogs implemented.
- [x] Setup-state migration implemented.
- [x] Existing flows integrated without flag breakage.
- [x] Focused and full verification run.
- [x] Live interactive wizard smoke run.
- [x] Final review completed.
