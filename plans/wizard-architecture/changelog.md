# Changelog Notes

## Added

- Shared wizard planning contracts and scenario catalogs.
- Wizard inventory and preview helpers for setup/profile/channel flows.

## Changed

- Setup state is upgraded additively with wizard metadata and workspace scope.
- Wizard labels/previews explain scenario intent without changing stored raw values.
- Active channel-owned tools are approved through a dedicated trusted channel context instead of the generic CLI approval path.
- Telegram, Telethon, and PartyFlow active channel turns can default `channel.send` to the current endpoint/peer without requiring the model to see or call generic `app.run`.

## Migration

- Existing setup state is migrated idempotently during `afk upgrade apply`.
- Existing profile policies and channel endpoint configs remain compatible.
- Legacy restricted-shell profiles are upgraded to require a shell sandbox when their file scope cannot be safely enforced without one.
- Legacy shell profiles with empty allowed-dir snapshots are treated as profile-only restricted scope during upgrade.

## Security

- Channel-scoped tools remain endpoint-scoped and do not imply generic `app.run`, filesystem, or shell access.
- Generic CLI-approved tools keep their existing behavior; channel-owned tool grants require an active channel runtime context.
- PartyFlow CLI readiness probes use operator credentials directly and no longer force safe channel profiles to expose `app.run`.
