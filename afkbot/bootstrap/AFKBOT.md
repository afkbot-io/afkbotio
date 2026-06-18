# AFKBOT

AFKBOT is a profile-centric local agent runtime. A profile is the security,
identity, memory, credential, skill, subagent, MCP, channel, and tool boundary.
A session is one conversation inside a profile. External channels route messages
into a profile/session; they do not own the agent identity.

Read this file before changing AFKBOT internals. Then read the focused files:

- `afkbot/bootstrap/AFKBOT_PLUGINS.md` for embedded plugins, plugin scaffolds,
  plugin-provided skills/tools/apps, and install/update flows.
- `afkbot/bootstrap/AFKBOT_CHANNELS.md` for Telegram, Telethon, PartyFlow, access
  policy, channel routing, `channel.send`, and custom-channel constraints.

## Runtime Map

- Bootstrap instructions: `afkbot/bootstrap/*.md`.
- Context assembly: `afkbot/services/agent_loop/context_builder.py`.
- Profiles and profile-local bootstrap overrides:
  `afkbot/services/profile_runtime/*` and `afk profile bootstrap ...`.
- Skills: bundled in `afkbot/skills/*/SKILL.md`, profile-managed through
  `afk skill ...`, and plugin-provided through plugin skill dirs.
- Subagents: bundled in `afkbot/subagents/*.md`, profile-managed through
  `afk subagent ...`.
- Task Flow employees: profile-managed markdown descriptors under
  `profiles/<profile_id>/employees/*.md`; these are the durable organization
  roles used by Task Flow, distinct from CLI subagents.
- Tool plugins exposed to the agent: `afkbot/services/tools/plugins/*`.
- Embedded platform plugins: `afkbot/services/plugins/*`.
- Channels and external ingress: `afkbot/services/channels/*` and
  `afkbot/services/channel_routing/*`.
- Runtime MCP bridge: `afkbot/services/mcp_integration/*`,
  `afkbot/services/mcp_runtime/*`, and the bundled `mcp-manager` skill.

## Bootstrap Files

Core bootstrap files are bundled under `afkbot/bootstrap`. They are package data
and are read by `ContextBuilder` according to `Settings.bootstrap_files`.
Additional markdown files in this directory are not automatically included in
every turn unless `Settings.bootstrap_files` includes them, but `AGENTS.md`
points agents to them so they can read the right local reference before making
changes.

Profile bootstrap files overlay core bootstrap files. Do not assume a profile
has default behavior after a profile override exists; inspect profile bootstrap
when the active profile matters.

## Change Workflow

1. Inspect the closest existing service, CLI command, tests, and README section.
2. Prefer existing service boundaries over adding cross-cutting shortcuts.
3. Keep profile boundaries intact: profile id, session id, credentials, memory,
   skills, subagents, and channel routing must stay scoped.
4. For new runtime surfaces, add deterministic errors and tests for unsupported
   or unsafe paths.
5. Update agent-facing docs when the change affects how future agents should
   extend plugins, channels, skills, subagents, MCP, or bootstrap behavior.

## Verification

Use focused tests for the touched surface. Common starting points:

- Plugin runtime: `uv run pytest tests/services/plugins/test_plugin_service.py`.
- Channel runtime: `uv run pytest tests/services/channels tests/cli/channels`.
- Agent context: `uv run pytest tests/services/agent_loop/test_context_builder.py`.
- CLI command surfaces: inspect the matching `tests/cli/...` package.
