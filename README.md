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
afk setup
afk chat
```

Hosted installer for Windows PowerShell:

```powershell
powershell -c "irm https://afkbot.io/install.ps1 | iex"
afk setup
afk chat
```

Local installer from a source checkout:

```bash
bash scripts/install.sh --repo-url "file://$PWD"
afk setup
afk chat
```

The installer is idempotent. Rerun it to refresh the installed tool in place, or use `afk update`.

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
curl -fsS http://127.0.0.1:8081/healthz
```

Webhook trigger example:

```bash
curl -X POST http://127.0.0.1:8080/v1/automations/<profile_id>/webhook/<token> \
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
uv run afk profile show default
uv run afk update
```

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
uv tool upgrade afkbotio --reinstall
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
