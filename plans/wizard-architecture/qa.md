# QA

## Preconditions

- Source checkout with `uv sync --extra dev`.
- Isolated temp root for CLI smoke tests.

## Positive Scenarios

- `afk setup --yes --accept-risk --skip-llm-token-verify` creates setup state with wizard metadata.
- `afk profile add ops --yes ... --policy-workspace-scope profile_only` still works.
- `afk channel telegram add ... --yes` preserves existing defaults and output.
- `afk channel partyflow add ... --yes` prints local/public webhook URL behavior unchanged.
- `afk upgrade inspect/apply` migrates legacy setup state without changing runtime permissions unexpectedly.

## Negative Scenarios

- Shell scenario with restricted dirs and no sandbox backend warns/fails closed as before.
- `channel.history.list` remains current-channel scoped.
- `channel.send` cannot target outside outbound allowlist.
- Old setup state without wizard fields is accepted.

## Verification Commands

```bash
uv run --extra dev ruff check afkbot tests
uv run --extra dev mypy afkbot tests
uv run --extra dev python -m pytest tests/services/wizard tests/services/setup tests/services/upgrade tests/cli/channels/test_wizard_copy.py -q
uv run --extra dev python -m pytest -q
git diff --check
```

## Latest Results

- Live interactive smoke used temp root `/var/folders/hq/150r_qxx3bn4mw1lbwv8jz8m0000gn/T/tmp.l0h0wj212j`.
- `afk setup --accept-risk --skip-llm-token-verify --llm-provider openai --chat-model gpt-4o-mini --lang en`: custom path, strict policy, sandbox-shell scenario, `macos-sandbox-exec` backend, and final English preview worked.
- `afk profile add ops --name Ops --llm-provider openai --chat-model gpt-4o-mini --skip-llm-token-verify --lang ru`: Russian question set and Task Flow channel scenario worked; resulting profile keeps file/shell/app tools disabled.
- `afk channel partyflow add live-partyflow --profile ops --lang ru`: Russian channel wizard worked with keyword trigger, webhook-only ingress, outbound allowlist, ingress batching, optional blank signing secret, and local webhook URL.
- `afk channel telegram add live-telegram --profile ops --lang en`: English channel wizard worked with group mention trigger, allowlists, outbound restriction, and visible `channel.send` preview.
- `afk profile show ops`, `afk channel partyflow show live-partyflow`, `afk channel partyflow webhook-url live-partyflow`, `afk channel telegram show live-telegram`, and `afk sandbox status` confirmed persisted profile/channel settings.
- `afk channel partyflow status live-partyflow --probe` with fake credentials no longer fails as `profile_policy_violation`; it reaches the PartyFlow API path and returns the expected fake-token `partyflow_unauthorized`.
- `uv run --extra dev python -m pytest tests/cli/profile_cli/test_helpers.py tests/services/wizard/test_catalogs.py tests/services/upgrade/test_service.py::test_upgrade_service_enforces_required_shell_sandbox_for_legacy_restricted_shell tests/services/upgrade/test_service.py::test_upgrade_service_enforces_required_shell_sandbox_for_empty_legacy_scope tests/services/agent_loop/test_loop_llm.py::test_llm_visible_tools_include_explicitly_approved_tool_without_bypassing_deny_rules tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_passes_cli_policy_tool_approval_override tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_filters_channel_owned_generic_approval tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context -q`: 21 passed.
- `uv run --extra dev python -m pytest tests/cli/channels/test_partyflow_add.py::test_channel_partyflow_status_probe_is_not_blocked_by_profile_app_policy tests/services/tools/test_channel_send_tool.py tests/services/agent_loop/test_loop_policy_gates.py::test_telegram_active_channel_send_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_tool_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context -q`: 17 passed.
- `uv run --extra dev python -m pytest tests/services/wizard tests/services/upgrade/test_service.py tests/services/setup/test_state.py tests/services/setup/test_policy_inputs.py tests/cli/profile_cli/test_helpers.py tests/services/agent_loop/test_loop_llm.py::test_llm_visible_tools_include_explicitly_approved_tool_without_bypassing_deny_rules tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_passes_cli_policy_tool_approval_override tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_filters_channel_owned_generic_approval tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_tool_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_context_survives_agent_loop_resolution tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_approval_requires_trusted_channel_context tests/cli/channels/test_wizard_copy.py tests/cli/channels/test_telegram_add.py tests/cli/channels/test_telethon_add.py tests/cli/channels/test_partyflow_add.py -q`: 86 passed.
- `uv run --extra dev ruff check afkbot tests`: passed.
- `uv run --extra dev mypy afkbot tests`: passed.
- `uv run --extra dev python -m pytest -q`: 2023 passed, 1 skipped, 2 existing `aiosqlite` thread warnings in `tests/cli/channels/test_telegram_add.py::test_channel_telegram_add_accepts_group_trigger_mode`.
- `git diff --check`: passed.
- Isolated CLI smoke with temp `AFKBOT_ROOT_DIR`: `afk setup --yes ...`, `afk channel partyflow add ... --yes`, and `afk channel partyflow webhook-url ops-partyflow` passed; output included a local loopback webhook URL.
- Latest isolated CLI smoke with temp `AFKBOT_ROOT_DIR`: `afk setup --yes --accept-risk --skip-llm-token-verify`, `afk channel partyflow add smoke-partyflow --yes`, `afk channel partyflow webhook-url smoke-partyflow`, and `afk channel partyflow status smoke-partyflow` passed; output included local loopback webhook URL and channel preview.

## Residual Warnings

- Latest full pytest run emitted two existing-looking `aiosqlite` thread warnings in a Telegram channel CLI test; no failures were observed.
