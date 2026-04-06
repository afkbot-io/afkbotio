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
curl -fsSL https://afkbot.io/install.sh | bash -s -- --git-ref v1.0.6

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
- `afk doctor` prints the effective runtime/chat ports and checks local readiness
- `afk chat` is the main entrypoint for real work

The runtime chooses and persists a non-default local port automatically for fresh installs, so use `afk doctor` when you need the actual `runtime_port` or `api_port`.

Manual local source setup with `uv`:

```bash
uv sync --extra dev
uv run afk setup
uv run afk doctor
uv run afk chat
```

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
uv run afk start
uv run afk doctor
# doctor prints the effective runtime_port and api_port for this install
```

Webhook trigger example:

```bash
curl -X POST http://127.0.0.1:<runtime_port>/v1/automations/<profile_id>/webhook/<token> \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"manual-test-1"}'
```

Useful commands:

```bash
uv run afk version
uv run afk doctor
uv run afk setup
uv run afk chat --message "Summarize this project"
uv run afk automation list --profile default
uv run afk mcp list
uv run afk profile show default
uv run afk update
```

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

- Environment-based configuration examples live in [`.env.example`](.env.example).
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

## License

AFKBOT is distributed under the `Sustainable Use License 1.0`.

- See [`LICENSE`](LICENSE) for the full license text.
- See [`LICENSE_FAQ.md`](LICENSE_FAQ.md) for practical allowed/not-allowed examples.
- See [`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md) for commercial-use guidance.
- See [`TRADEMARKS.md`](TRADEMARKS.md) for brand and name usage rules.
