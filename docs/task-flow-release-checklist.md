# Task Flow Release Checklist

Date: 2026-04-05

This checklist is for release verification of the current `Task Flow` slice.

## 1. Deterministic Smoke Check

Run the local smoke script first. It does not require a live LLM provider.

```bash
uv run python scripts/task_flow_release_smoke.py
```

Expected result:

- exit code `0`
- JSON output with `"ok": true`
- all flags under `"checks"` are `true`

What it covers:

- flow creation
- task creation
- dependency unblock
- review request-changes path
- append-only comments
- human inbox summary
- board generation
- stale-claim detection and repair

## 2. Runtime Pickup Check

Verify detached AI execution against a real configured profile.

Start the runtime:

```bash
uv run afk start
```

Create one AI-owned task:

```bash
uv run afk task create \
  --title "Release pickup smoke" \
  --description "Summarize this request in one short sentence and finish." \
  --owner-type ai_profile \
  --owner-ref default
```

Inspect the backlog:

```bash
uv run afk task board
uv run afk task run-list
```

Expected result:

- the task briefly appears in `running` or is already `completed`
- `afk task run-list` shows one detached attempt
- `afk task event-list <task_id>` includes `execution_completed` or `execution_review_ready`

## 3. Human Review Check

Create one review task:

```bash
uv run afk task create \
  --title "Release review smoke" \
  --description "Prepare review-ready output." \
  --owner-type ai_profile \
  --owner-ref default \
  --reviewer-type human \
  --reviewer-ref cli_user:$(whoami) \
  --requires-review
```

After runtime pickup, inspect:

```bash
uv run afk task review-list --actor-type human --actor-ref cli_user:$(whoami)
```

Then test both review actions:

```bash
uv run afk task review-request-changes <task_id> --reason-text "Smoke check changes request."
uv run afk task review-approve <task_id>
```

Expected result:

- `review-list` shows the task while it is in `review`
- `review-request-changes` moves it to `blocked`
- `review-approve` moves a review task to `completed`

## 4. Human Inbox Check

Inspect the startup-style inbox:

```bash
uv run afk task inbox --owner-ref cli_user:$(whoami)
uv run afk chat
```

Expected result:

- inbox JSON contains current `todo` / `blocked` / `review` counts
- `afk chat` starts with the assistant-style Task Flow notice when there is human-owned or reviewer-routed work

## 5. Stale Claim Repair Check

This is already covered by the smoke script. For operator UX only, verify the surfaces exist:

```bash
uv run afk task stale-list
uv run afk task stale-sweep
```

Expected result:

- commands succeed
- if no stale claims exist, they return an empty list / zero repaired count

## 6. Final Release Gate

Run the standard validation suite:

```bash
uv run --extra dev ruff check afkbot tests
uv run --extra dev mypy afkbot tests
uv run --extra dev pytest -q
```

Expected result:

- `ruff`: clean
- `mypy`: clean
- `pytest`: green
- only the known unrelated `aiosqlite` worker-thread warning may remain in channel/telethon contour
