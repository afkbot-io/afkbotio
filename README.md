# AFKBOT

AFKBOT is a source-available local AI runtime and CLI for chat-driven workflows,
tool calling, automations, and profile-scoped agent environments.

Documentation lives at [afkbot.io/docs](https://afkbot.io/docs). The project site is [afkbot.io](https://afkbot.io).
Use the docs site for setup, configuration, MCP, automations, and command reference.

## What AFKBOT does

- Runs local chat sessions with tool access, planning, and configurable reasoning.
- Supports multiple LLM providers in setup/profile runtime (`openrouter`, `openai`, `claude`, `moonshot`, `deepseek`, `xai`, `qwen`, and `custom`).
- Provides a CLI-first workflow for setup, chat, health checks, and runtime control.
- Supports profile-scoped configuration, secrets, permissions, and tool exposure.
- Includes browser, web, app, MCP, automation, and channel integration surfaces.
- Exposes a local runtime and API layer for longer-running workflows.

## Runtime Model

AFKBOT uses one session-oriented execution model across chat, API, automations,
Task Flow workers, and child subagents.

- One active turn runs at a time for each `(profile_id, session_id)`.
- If you send another message while a turn is still running, the next message is
  queued and starts after the current turn releases the session slot.
- `afk chat` planning modes control whether the agent starts with a read-only
  planning pass before execution:
  - `off`: execute immediately
  - `auto`: use plan-first for complex requests
  - `on`: always show a plan first, then execute
- A public `plan -> execute` flow runs inside the same serialized session slot,
  so execution starts automatically after planning unless you explicitly asked
  for only a plan.
- Inside one turn, the agent can fan out independent work in parallel with
  `session.job.run`, wait for every child job to finish, and then return one
  final answer.
- Subagents and Task Flow runs use separate child sessions, so they do not steal
  the parent chat session slot.

## Choosing the Execution Path

Use this mental model:

| Path | Use it when | Wait for the answer now? | Durable state? | Typical outcome |
| --- | --- | --- | --- | --- |
| `Chat turn` | The work fits in one bounded conversation turn | Yes | No | Plan, inspect files, run tools, answer in chat |
| `session.job.run` + subagents | You want parallel work inside the current turn | Yes | No | Fan out independent bash or subagent jobs, wait for all, merge results |
| `Task Flow` | The work is long-running, needs dependencies, review, handoff, or a backlog trail | Not necessarily | Yes | Create durable tasks, run them in background, inspect task runs and comments later |

Command examples below use the installed `afk` binary. If you are working from a source checkout without installing AFKBOT into your shell yet, run the same commands with `uv run`, for example `uv run afk doctor`.

Subagents are profile-local runtime assets, not global assistant personas. List
the subagents that the current AFKBOT profile can actually run with:

```bash
afk subagent list --profile default
```

## Chat And Planning

`afk chat` is the main orchestrator. It decides whether to stay in one turn,
fan out parallel jobs, or create durable Task Flow work.

Planning mode examples:

```bash
afk chat --plan off
afk chat --plan auto
afk chat --plan on
```

Behavior:

- `off`: the turn executes immediately.
- `auto`: the runtime may do a read-only planning pass for multi-step work.
- `on`: the runtime always shows a plan first and then executes in the same
  request.
- If you explicitly ask only for a plan, AFKBOT returns the plan and stops
  without starting execution.

## License Model

- AFKBOT source code is available under the `Sustainable Use License 1.0`.
- Personal use, non-commercial use, and internal business use are allowed.
- Forking and modifying AFKBOT are allowed, but redistribution must stay free of charge and non-commercial.
- You may not sell AFKBOT, sell copies of AFKBOT, resell the source code, or offer AFKBOT as a paid hosted or white-label service without separate permission.
- The repository license does not grant any trademark rights to the AFKBOT name, logo, or branding.

## Requirements

- Python 3.12 or newer for manual source installs
- `uv` recommended for local development
- SQLite is the default runtime database for AFKBOT
- The hosted installers bootstrap `uv`, install AFKBOT as an isolated uv tool, and keep runtime state outside the app source tree

## Install

Hosted installer for macOS/Linux:

```bash
curl -fsSL https://afkbot.io/install.sh | bash
# open a new terminal after install
afk setup
afk doctor
afk chat
```

Hosted installer for Windows PowerShell:

```powershell
powershell -c "irm https://afkbot.io/install.ps1 | iex"
# open a new terminal after install
afk setup
afk doctor
afk chat
```

Local installer from a source checkout:

```bash
bash scripts/install.sh --repo-url "file://$PWD"
# open a new terminal after install
afk setup
afk doctor
afk chat
```

Common installer flags:

```bash
# installer and setup prompts in Russian
curl -fsSL https://afkbot.io/install.sh | bash -s -- --lang ru

# install from a specific Git ref
curl -fsSL https://afkbot.io/install.sh | bash -s -- --git-ref v1.4.0

# install from a local checkout
bash scripts/install.sh --repo-url "file://$PWD"

# show actions without mutating the machine
bash scripts/install.sh --dry-run

# skip bootstrap-only setup seeding during install
bash scripts/install.sh --skip-setup
```

What the installer does:

- bootstraps `uv` into the user-local bin directory if needed
- installs AFKBOT as an isolated `uv tool`
- updates shell integration so `afk` is available in new terminals
- seeds the runtime root with bootstrap-only setup metadata
- remembers the install source so `afk update` can refresh the same source later

The installer is idempotent. Rerun it to refresh the installed tool in place, or use `afk update`.

## First Run

For normal usage, the first-run flow is:

```bash
afk setup
afk doctor
afk chat
```

- `afk setup` configures the default profile, provider, policy, locale, and runtime defaults
- `afk setup` also asks whether `afk chat` should check for AFKBOT updates before opening chat
- `afk doctor` prints the effective runtime/chat ports and checks local readiness
- `afk chat` is the main entrypoint for real work

Setup and profile policy directly control the tool surface that the runtime can
use. In practice:

- enable `Shell` if you want the agent to run shell commands or parallel bash
  jobs through `session.job.run`
- enable `Subagents` if you want the agent to run profile-local subagents
- enable `Task Flow` if you want durable backlog tasks, dependencies, review,
  and background execution
- enable `MCP`, `Browser`, `HTTP`, `Apps`, and other capability groups only for
  the surfaces you actually want exposed to the profile

Useful first-run checks:

```bash
afk doctor
afk profile show default
afk subagent list --profile default
afk task board --profile default
```

`afk profile show default` lets you confirm the effective runtime policy and
capabilities. `afk subagent list --profile default` shows the actual subagent
names that this profile can run.

If update notices are enabled in setup, interactive `afk chat` checks for a newer AFKBOT build before opening the session and asks:

- `Yes`
- `No`
- `Remind in a week`

`No` continues into chat immediately and does not save a permanent skip. `Remind in a week` suppresses all update prompts for seven days. If you disable update notices in setup, chat will not ask at startup.

The runtime chooses and persists a non-default local port automatically for fresh installs, so use `afk doctor` when you need the actual `runtime_port` or `api_port`.

Manual local source setup with `uv`:

```bash
uv sync --extra dev
afk setup
afk doctor
afk chat
```

If the checkout is not installed into your shell PATH, run the same commands with `uv run afk ...` from the repository root.

Manual local source setup with `pip`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
afk setup
afk doctor
afk chat
```

## Local Runtime

AFKBOT uses one local SQLite database by default for runtime state, semantic memory, and chat metadata:

```bash
export AFKBOT_DB_URL='sqlite+aiosqlite:///./afkbot.db'
```

Start the local runtime/API:

```bash
afk start
afk doctor
# doctor prints the effective runtime_port and api_port for this install
```

`afk start` launches the local runtime stack, including API routes, automation
delivery, and Task Flow background workers.

Webhook trigger example:

```bash
curl -X POST http://127.0.0.1:<runtime_port>/v1/automations/<profile_id>/webhook/<token> \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"manual-test-1"}'
```

Useful commands:

```bash
afk version
afk doctor
afk setup
afk chat --message "Summarize this project"
afk automation list --profile default
afk plugin list
afk mcp list
afk profile show default
afk update
```

## Chat Examples

Paste prompts like these into `afk chat`.

Parallel work inside one turn:

```text
Do one session.job.run call.
Run 2 bash jobs in parallel:
1) sleep 5 && echo FIRST
2) sleep 5 && echo SECOND
Wait for both and summarize the result.
```

Parallel profile-local subagents inside one turn:

```text
Do one session.job.run call.
Run 2 subagent jobs in parallel:
1) subagent_name=poet-10-lines, prompt="Write 10 lines about orchestration"
2) subagent_name=ui-reviewer, prompt="Review: button text has low contrast"
Wait for both and merge the results.
```

Durable work as Task Flow instead of one large chat turn:

```text
Break this project into durable Task Flow work:
- create a flow
- create the tasks
- add dependencies
- assign AI-owned tasks to the default profile
- leave me with the task ids and next review points
```

Rule of thumb:

- keep work in one chat turn when you want the answer now
- use `session.job.run` when the current turn contains independent parallel work
- use `Task Flow` when the work must survive the current chat session and keep a
  durable execution trail

## Plugins

AFKBOT supports installable embedded plugins that extend the local platform with:

- API routers
- static web apps
- tool factories
- skill directories
- app registrars
- optional startup and shutdown hooks

Current curated plugins:

- `afkbotui`: unified AFKBOT web workspace for automations today and future operator surfaces

Typical operator flow:

```bash
afk plugin list
afk plugin install
afk plugin inspect afkbotui
afk plugin config-get afkbotui
afk plugin update afkbotui
```

`afk plugin install` now works as a small wizard:

- it shows curated plugins that are not installed yet
- today the curated list contains only `afkbotui`
- the last option is a custom GitHub source, where you can paste a GitHub URL or `github:owner/repo@ref`

You can still install directly without the wizard:

```bash
afk plugin install github:afkbot-io/afkbotuiplugin@main
```

Direct `afk plugin install <source>` also still accepts a local path when you want to install a plugin from a checkout on disk.

The current curated external plugin is AFKBOT UI. Today it provides the web workspace for automations and is intended to expand into the main operator surface for Task Flow, subagents, MCP, AI settings, and profile management. The older kanban-specific example is no longer the curated plugin path. After installation and `afk start`, it mounts:

- API: `/v1/plugins/afkbotui/...`
- UI: `/plugins/afkbotui`

Plugin install state lives under the AFKBOT runtime root in `/plugins/...` and is treated as local machine state, not repository content.

## Browser UI Auth

AFKBOT can now protect browser plugin UIs and their plugin API routes with one
operator password managed at the core runtime level.

- Configure it with `afk auth setup` or `afk auth create`.
- Inspect or update the policy with `afk auth status`, `afk auth update`, and
  `afk auth rotate-password`.
- Disable it with `afk auth disable`.
- Protection applies only to plugin surfaces that opt in through
  `auth.operator_required` or are explicitly listed with `--protected-plugin-id`.
- Protected browser surfaces redirect to `/auth/login`, and only the matching
  protected plugin API routes return `401` until the operator session is
  established.
- Protection follows each plugin's declared API and web mount prefixes, so
  custom prefixes such as `/internal/...` or `/ui/...` are covered too.
- Password hashes and cookie keys live in encrypted runtime secrets, not inside
  plugin packages or plugin config JSON.

## Channels Quickstart

AFKBOT can attach chat transports to a profile for inbound routing and operator workflows.
Use the docs site for the full command reference; the examples below cover the common setup paths.

Telegram bot polling channel:

```bash
# guided wizard; omit channel_id to let AFKBOT suggest one
afk channel telegram add

# fully explicit example
afk channel telegram add support-bot --profile default --credential-profile support-bot
afk channel telegram status
afk channel telegram show support-bot
```

Telethon user-account channel:

```bash
# guided wizard; omit channel_id to let AFKBOT suggest one
afk channel telethon add

# fully explicit example
afk channel telethon add personal-user --profile default --credential-profile personal-user
afk channel telethon status --probe
afk channel telethon show personal-user
```

Notes:

- Interactive channel setup explains required credentials inline: Telegram bot token comes from `@BotFather`; Telethon `api_id` and `api_hash` come from `my.telegram.org`.
- If you skip the Telethon session string during setup, finish login later with `afk channel telethon authorize <channel_id>`.
- Interactive prompt language follows this order: explicit `--lang` or `--ru`, then the project's saved `prompt_language`, then the current system locale.

## MCP Quickstart

AFKBOT supports profile-local MCP configuration plus runtime MCP tool discovery.

Manual CLI flow:

```bash
# connect one MCP endpoint URL to the default profile
afk mcp connect https://example.com/mcp --profile default --secret-ref mcp_example_token

# inspect the saved config
afk mcp get example --profile default

# validate effective MCP files for the profile
afk mcp validate --profile default

# list all saved MCP servers, including disabled entries
afk mcp list --profile default --show-disabled
```

You can still use the explicit form:

```bash
afk mcp add --profile default --url https://example.com/mcp --secret-ref mcp_example_token
```

Chat-driven flow:

```text
Connect this MCP endpoint to my default profile: https://example.com/mcp
Show me what was saved and validate it.
```

Notes:

- Use the actual MCP endpoint URL, not a generic product homepage.
- `afk mcp` and `mcp.profile.*` manage profile config.
- `mcp.tools.list` and `mcp.tools.call` are the runtime bridge used after a compatible remote MCP server is configured and exposed.
- If the MCP server needs auth, store only refs in MCP config such as `secret_refs` or `env_refs`; do not hardcode plaintext secrets into MCP JSON.

Managed-install maintenance:

```bash
afk update
bash scripts/uninstall.sh --yes
```

```powershell
afk update
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1 -Yes
```

Hosted installers use `uv tool install` under the hood. Advanced equivalents:

```bash
uv tool install --python 3.12 --reinstall https://github.com/afkbot-io/afkbotio/archive/main.tar.gz
afk update
uv tool uninstall afkbotio
```

## Configuration

- Environment-based configuration examples live in [`.env.example`](https://github.com/afkbot-io/afkbotio/blob/main/.env.example).
- Setup/provider selection supports OpenRouter, OpenAI, Claude, Moonshot (Kimi), DeepSeek, xAI, Qwen, and custom OpenAI-compatible endpoints.
- Runtime secrets should be configured through `afk setup`, `afk profile`, or credential commands, not committed into the repository.
- Manual source setups use a local SQLite database and a local AFKBOT runtime.
- New installs create the current SQLite schema directly; no legacy migration chain is required.
- Full setup guidance and user documentation are published at [afkbot.io/docs](https://afkbot.io/docs).

## Development

Install the development environment and run the standard checks:

```bash
uv sync --extra dev
uv run ruff check afkbot tests
uv run mypy afkbot tests
uv run pytest -q
```

## PyPI Release

The project builds clean Python distributions and passes `twine check`:

```bash
uv build
uvx twine check dist/*
```

For a safe dry run, upload to TestPyPI first:

```bash
uvx twine upload --repository testpypi dist/*
```

This repository also includes a GitHub Actions publish workflow prepared for trusted publishing:

- `workflow_dispatch`: builds distributions, runs `twine check`, and publishes to `testpypi`
- `push` on `v*` tags: verifies the tag matches `project.version`, builds distributions, runs `twine check`, attaches them to the GitHub release, and publishes to `pypi`

Before using the workflow, create matching trusted publishing environments in PyPI:

- `testpypi` for `https://test.pypi.org/p/afkbotio`
- `pypi` for `https://pypi.org/project/afkbotio/`

## License

AFKBOT is distributed under the `Sustainable Use License 1.0`.

- See [`LICENSE`](https://github.com/afkbot-io/afkbotio/blob/main/LICENSE) for the full license text.
- See [`LICENSE_FAQ.md`](https://github.com/afkbot-io/afkbotio/blob/main/LICENSE_FAQ.md) for practical allowed/not-allowed examples.
- See [`COMMERCIAL_LICENSE.md`](https://github.com/afkbot-io/afkbotio/blob/main/COMMERCIAL_LICENSE.md) for commercial-use guidance.
- See [`TRADEMARKS.md`](https://github.com/afkbot-io/afkbotio/blob/main/TRADEMARKS.md) for brand and name usage rules.
