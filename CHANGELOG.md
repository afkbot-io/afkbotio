# Changelog

All notable changes to this project will be documented in this file.

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
