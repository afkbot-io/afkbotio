# Diagnostic Error Logs

This note documents the current diagnostic error log behavior implemented in AFKBOT and the operator-facing `afk logs` commands.

## What exists now

- AFKBOT writes persistent error logs under `<AFKBOT_ROOT_DIR>/logs/`.
- Logs are grouped by component. The file layout is:

```text
<AFKBOT_ROOT_DIR>/logs/<component>/errors.log
```

- Component names are normalized before use as paths. Unsafe characters are replaced with `-`.
- Error files are rotated with these current limits:
  - current file: up to `1,000,000` bytes
  - rotated backups per file: `5`
- Log output is redacted before it is written. Common secret-like values such as API keys, bearer tokens, passwords, cookies, and token fields are replaced with `[REDACTED]`.

## Runtime root and path rules

- In a source checkout, the default runtime root is the repository root.
- You can override it with `AFKBOT_ROOT_DIR`.
- The log directory path printed by `afk logs path` is the exact directory used by the runtime:

```bash
afk logs path
```

## `afk logs` commands

`afk logs` is available without completing `afk setup`.

### `afk logs`

- Prints the log directory.
- Prints every `errors.log` and rotated `errors.log.*` file found under `logs/`.
- Files are listed from newest modification time to oldest.
- Output format is:

```text
Log directory: /path/to/runtime/logs
api/errors.log  1234 bytes  modified 2026-04-27 07:42:13 UTC
```

### `afk logs path`

- Prints only the runtime log directory path.

### `afk logs list`

- Same summary output as bare `afk logs`.

### `afk logs tail`

- Prints the selected file path first, then the newest lines from that file.
- Default line count is `80`.
- Allowed range for `--lines` is `1..1000`.

Examples:

```bash
afk logs tail
afk logs tail --component api
afk logs tail --component taskflow --lines 200
```

Important behavior:

- `afk logs tail` without `--component` reads the newest log file by modification time.
- CLI crashes are written to `logs/cli/errors.log`; normal successful CLI commands do not create that file.
- If you need deterministic output for API, runtime, task flow, or tools failures, pass `--component`.
- When the selected file does not exist, the command prints:

```text
No log file found. Log directory: /path/to/runtime/logs
```

### `afk logs clean`

- Deletes all current and rotated AFKBOT error log files returned by the log scanner.
- Requires explicit confirmation:

```bash
afk logs clean --yes
```

- Without `--yes`, the command refuses to run and exits with status code `2`.
- Running another successful `afk logs ...` command after cleanup should not recreate `logs/cli/errors.log`; that file appears only when a CLI exception is logged.

## Known component names in current code

These components are confirmed in the current tree:

- `api`
- `cli`
- `runtime`
- `taskflow`
- `tools`

Other components may appear if more call sites start logging through the same helper.

## Operator workflow

For task failures or generic "run `afk logs`" guidance:

1. Run `afk logs` to see the runtime log directory and current files.
2. Run `afk logs tail --component taskflow` for task creation or Task Flow failures.
3. Run `afk logs tail --component api` for HTTP/API failures.
4. Run `afk logs tail --component cli` when the command itself crashes before deeper runtime work starts.
5. Use `afk logs clean --yes` only when you intentionally want to remove current and rotated diagnostic files.

## Git note

`docs/` is currently ignored by `.gitignore`, so this file will need forced staging when included in a PR:

```bash
git add -f docs/diagnostic-logs.md
```
