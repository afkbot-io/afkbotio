# Contributing

## Getting Started

1. Install Python 3.12 or newer.
2. Install dependencies with `uv sync --extra dev`.
3. Run `uv run afk setup` to configure a local profile.
4. Run `uv run afk doctor` to confirm the local environment is healthy.

## Licensing and Contributions

- AFKBOT is source-available, not OSI open source.
- The repository is licensed under the `Sustainable Use License 1.0`.
- By submitting a pull request, you agree to the terms in [`CONTRIBUTOR_LICENSE_AGREEMENT.md`](CONTRIBUTOR_LICENSE_AGREEMENT.md).
- Contributions must not add code or assets that you do not have the right to contribute under these terms.

## Development Workflow

- Keep changes focused and easy to review.
- Prefer small pull requests over large mixed refactors.
- Update user-facing docs when behavior or setup changes.
- Do not commit secrets, local runtime state, or generated artifacts.

## Checks

Run the standard checks before opening a pull request:

```bash
uv run ruff check afkbot tests
uv run mypy afkbot tests
uv run pytest -q
```

For setup/runtime changes, also run a local source smoke flow:

```bash
uv run afk setup --yes --accept-risk --skip-llm-token-verify
uv run afk profile show default
uv run afk doctor
```

## Pull Requests

- Describe the problem and the change clearly.
- Call out breaking changes, migrations, or config changes.
- Link related issues when applicable.
- Keep commit history readable.
- Update licensing or policy docs when a change affects redistribution, usage rights, or security posture.

## Issues

- Use issues for bugs, feature requests, and documentation problems.
- Include reproduction steps, environment details, and expected behavior.
- Remove secrets, tokens, and private URLs before posting logs.
