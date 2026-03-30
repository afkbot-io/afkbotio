# Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - 2026-03-30

### Added

- `uv` tool-based hosted install/update/uninstall flow documentation and advanced command examples.
- Regression coverage for uv-tool installs, runtime/app path resolution, and safer installer migration behavior.

### Changed

- Hosted installers on macOS, Linux, and Windows now install AFKBOT through `uv tool install` instead of the previous managed snapshot/virtualenv flow.
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
