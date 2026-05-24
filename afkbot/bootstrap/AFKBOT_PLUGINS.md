# AFKBOT Plugins

Embedded plugins are installable runtime extensions. They are the supported
self-service path when the user wants AFKBOT to create and add a new operator
surface, API route, static web surface, app registrar, tool factory, lifecycle
hook, or plugin-provided skills.

## Current Capability

The plugin system is implemented in `afkbot/services/plugins/*`.

Important files:

- Contracts: `afkbot/services/plugins/contracts.py`.
- Runtime registration API: `afkbot/services/plugins/runtime_registry.py`.
- Install/load/update service: `afkbot/services/plugins/service.py`.
- Scaffold generator: `afkbot/services/plugins/scaffold.py`.
- CLI commands: `afkbot/cli/commands/plugin.py`.
- Tests: `tests/services/plugins/test_plugin_service.py`.

## Scaffold And Install

Create a starter plugin:

```bash
afk plugin scaffold ./my-plugin --plugin-id myplugin --name "My Plugin"
```

Useful scaffold flags:

- `--api-router/--no-api-router`
- `--static-web/--no-static-web`
- `--skills/--no-skills`
- `--tools/--no-tools`
- `--apps/--no-apps`
- `--lifecycle/--no-lifecycle`
- `--channel/--no-channel`

Install locally:

```bash
afk plugin install ./my-plugin
```

Install from GitHub archive syntax:

```bash
afk plugin install github:owner/repo@ref
```

Inspect and operate:

```bash
afk plugin list
afk plugin inspect myplugin
afk plugin enable myplugin
afk plugin disable myplugin
afk plugin update myplugin
afk plugin remove myplugin
```

## Plugin Layout

A plugin root must contain `.afkbot-plugin/plugin.json`. The scaffold also
creates:

- `python/afkbot_plugin_<plugin_id_with_hyphens_as_underscores>/plugin.py`
  as the entrypoint module.
- `web/dist` when static web is enabled.
- `skills/<plugin_id>/SKILL.md` when plugin-provided skills are enabled.
- `README.md` with local install notes.

The manifest declares:

- `plugin_id`, `name`, `version`, `afkbot_version`, `kind`, `entrypoint`.
- `capabilities`: `api_router`, `static_web`, `tools`, `skills`, `apps`,
  `lifecycle`, `channels`.
- `permissions`: `database`, `taskflow`, `outbound_http`, `data_dir_write`.
- `auth.operator_required` for protected web/API plugin surfaces.
- `mounts.api_prefix` and `mounts.web_prefix`.
- `paths.python_root`, `paths.web_root`, and `paths.skills_root`.
- `config_schema` plus `default_config`.

The entrypoint must be `module:function` and is called with a
`PluginRuntimeRegistry`.

## Runtime Registry API

Inside `register(registry)` a plugin can:

- `registry.register_router(router)` for FastAPI API routes.
- `registry.register_static_dir(path=..., mount_path=...)` for static assets.
- `registry.register_skill_dir(path)` for plugin-provided skills.
- `registry.register_tool_factory(factory_id, factory)` for runtime tools.
- `registry.register_app_registrar(registrar)` for app registry entries.
- `registry.register_channel_adapter(adapter)` for plugin-provided channel
  runtimes and outbound senders.
- `registry.register_startup_hook(hook)` and `registry.register_shutdown_hook(hook)`.
- `registry.read_config()`, `registry.write_config(...)`, and
  `registry.reset_config()` for validated plugin config.
- Use `registry.data_dir` for plugin-owned writable state.
- Use `registry.resolve_path(...)` for paths inside the installed plugin root.

Do not write outside the plugin root or plugin data dir. Do not bypass manifest
permissions; they are the operator review contract.

## How Agents Should Build Plugins

1. Use `afk plugin scaffold` unless the user explicitly provides a full plugin
   skeleton.
2. Edit the manifest first so capabilities, permissions, auth, mounts, paths,
   and config schema match the intended surface.
3. Implement the entrypoint using `PluginRuntimeRegistry`.
4. Add focused tests in or near `tests/services/plugins/test_plugin_service.py`
   when behavior changes.
5. Install with `afk plugin install <path> --overwrite` only after the plugin
   imports cleanly and the manifest validates.
6. Inspect with `afk plugin inspect <plugin_id>` and verify the enabled runtime
   surface.
7. For channel plugins, run `afk channel plugin adapters`, create one endpoint
   with `afk channel plugin add`, and test channel disable/delete before
   handing the integration back.

## Limits To Remember

- Plugin tools are tool factories, not channel adapters. Use
  `register_channel_adapter(...)` for transports that should participate in
  managed channel startup and shared `channel.send`.
- Plugin API/static surfaces are mounted by the core FastAPI app, but plugin
  registration should still not expose secrets or unauthenticated operator
  actions.
- Plugin-provided skills are discoverable through active plugin skill dirs after
  the plugin is installed and enabled.
- Manifest collisions are rejected for API prefixes, static mount paths, and tool
  factory ids. Channel adapter collisions are rejected by `(transport,
  adapter_kind)`, including collisions with built-in adapters.

## Channel Plugins

AFKBOT supports OpenClaw-style self-authored channel integrations through
`ChannelAdapterFactory` in `afkbot/services/channels/plugin_adapters.py`.

Create one with:

```bash
afk plugin scaffold ./plugins/avito-channel \
  --plugin-id avito \
  --name "Avito Channel" \
  --channel
```

The generated plugin:

- sets `capabilities.channels=true`;
- requests `outbound_http` and `data_dir_write` because provider polling/sending
  performs I/O and persists cursors;
- creates `python/afkbot_plugin_<id_with_hyphens_as_underscores>/channel.py`;
- registers a `ChannelAdapterFactory` from the plugin entrypoint.

A channel adapter can declare:

- channel metadata: `transport`, `adapter_kind`, label, and description;
- setup metadata: `setup_instructions` and `endpoint_config_schema` for
  `afk channel plugin adapters`; primitive schema fields can declare
  `type`, `default`, `required`, `choices`, numeric bounds, string bounds or
  regex, and `secret` for hidden interactive input;
- runtime handlers: start/stop polling or webhook workers, normalize inbound
  events, and dispatch them into the agent loop through
  `PluginChannelIngressDispatcher`;
- outbound handlers: send text/media to a target and return provider message ids;
- endpoint config validation: `validate_endpoint_config(endpoint)` before the
  generic CLI persists an endpoint;
- target grammar: validate provider-specific conversation ids into base chat,
  thread, user, address, and parent candidates;
- access policy keying: return the target id checked against
  `outbound_allow_to`.

After install/enable, create an endpoint:

```bash
afk channel plugin adapters
afk channel plugin add avito-main \
  --transport avito \
  --adapter-kind avito_polling \
  --profile default \
  --credential-profile avito-main \
  --account-id seller-1 \
  --allow-from buyer-1 \
  --outbound-allow-to conv-1 \
  --yes
```

Generic channel endpoint operations:

```bash
afk channel show avito-main
afk channel disable avito-main
afk channel enable avito-main
afk channel delete avito-main
```

Plugin-level operations are protected when endpoints still depend on the plugin:

```bash
afk plugin disable avito --force
afk plugin remove avito --delete-channel-endpoints
```

Current limits: provider config is stored in generic
`ChannelEndpointConfig.config`; `endpoint_config_schema` supports primitive
string/integer/number/boolean fields with defaults and interactive prompts, but
complex provider-specific setup wizards still belong in the plugin README or a
plugin API/UI surface. Plugin update compatibility checks only verify that
existing `(transport, adapter_kind)` keys remain registered; provider-specific
endpoint migration still belongs to the plugin author.
