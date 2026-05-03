# Tasks

## 1. Inventory and RED Tests

- Status: done
- Add tests that assert wizard inventory contains current setup/profile/channel questions in RU/EN.
- Add tests for scenario defaults and preview warning behavior.
- Verification: `uv run --extra dev python -m pytest tests/services/wizard tests/services/setup/test_state.py tests/services/upgrade/test_service.py -q`

## 2. Wizard Contracts

- Status: done
- Add `afkbot/services/wizard/` with `WizardQuestion`, `WizardBranch`, `WizardPlan`, `WizardPreview`, and stable scenario contracts.
- Keep renderer-independent definitions.
- Verification: unit tests for serializable inventories.

## 3. Scenario Catalogs

- Status: done
- Add profile scenario templates: `chat_only`, `taskflow_channel`, `project_readonly`, `sandbox_writer`, `sandbox_shell`, `trusted_admin`.
- Add channel scenario templates: `private_dm`, `group_mention`, `group_all_messages`, `webhook_mention`, `webhook_keywords`, `watcher_digest`, `trusted_admin`.
- Verification: scenario tests assert capabilities, file scope, shell sandbox, tool profiles, and branch visibility.

## 4. Preview Builder

- Status: done
- Add preview builder that explains profile ceiling, channel surface, credentials, filesystem, shell, network, and warnings.
- Integrate preview text into existing setup/profile/channel output where low-risk.
- Verification: snapshot-style tests for RU/EN preview lines.

## 5. Compatibility and Migration

- Status: done
- Add setup-state version upgrade preserving old fields and adding wizard metadata/workspace scope.
- Do not rewrite channel endpoint configs beyond canonical existing serializers.
- Verification: upgrade tests for v1 setup state and current setup state.

## 6. Command Integration

- Status: done
- Use shared labels/scenario defaults in prompt helpers.
- Keep existing raw flags and persisted values.
- Verification: existing CLI tests plus new non-interactive parity tests.

## 7. Review and Full Verification

- Status: done
- Run ruff, mypy, targeted tests, full pytest.
- Run code review pass for security, migrations, stale code, docs.

## Verification Evidence

- Live interactive smoke with `AFKBOT_ROOT_DIR=/var/folders/hq/150r_qxx3bn4mw1lbwv8jz8m0000gn/T/tmp.l0h0wj212j`:
  - `afk setup --accept-risk --skip-llm-token-verify --llm-provider openai --chat-model gpt-4o-mini --lang en`: navigated custom security, strict level, sandbox-shell scenario, update prompts; preview showed profile-only read/write files and required `macos-sandbox-exec`.
  - `afk profile add ops --name Ops --llm-provider openai --chat-model gpt-4o-mini --skip-llm-token-verify --lang ru`: Russian prompts rendered, Task Flow channel scenario created `memory,taskflow` profile with file/shell/app tools disabled.
  - `afk channel partyflow add live-partyflow --profile ops --lang ru`: Russian PartyFlow wizard rendered webhook-only ingress, keyword trigger, outbound allowlist, ingress batching, optional blank signing secret, local webhook URL, and channel-owned `channel.history.list, channel.send`.
  - `afk channel telegram add live-telegram --profile ops --lang en`: English Telegram wizard rendered group mention scenario, group/user allowlists, outbound allowlist, batching/humanize questions, and channel-owned `channel.send`.
  - `afk profile show ops`, `afk channel partyflow show live-partyflow`, `afk channel partyflow webhook-url live-partyflow`, `afk channel telegram show live-telegram`, and `afk sandbox status` confirmed persisted settings and sandbox backend.
- Live negative probe check: `afk channel partyflow status live-partyflow --probe` with a fake token now fails as `partyflow_unauthorized`, not `profile_policy_violation`; the operator probe is no longer blocked by safe channel profile app policy.
- `uv run --extra dev python -m pytest tests/cli/profile_cli/test_helpers.py tests/services/wizard/test_catalogs.py tests/services/upgrade/test_service.py::test_upgrade_service_enforces_required_shell_sandbox_for_legacy_restricted_shell tests/services/upgrade/test_service.py::test_upgrade_service_enforces_required_shell_sandbox_for_empty_legacy_scope tests/services/agent_loop/test_loop_llm.py::test_llm_visible_tools_include_explicitly_approved_tool_without_bypassing_deny_rules tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_passes_cli_policy_tool_approval_override tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_filters_channel_owned_generic_approval tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context -q`: 21 passed.
- `uv run --extra dev python -m pytest tests/cli/channels/test_partyflow_add.py::test_channel_partyflow_status_probe_is_not_blocked_by_profile_app_policy tests/services/tools/test_channel_send_tool.py tests/services/agent_loop/test_loop_policy_gates.py::test_telegram_active_channel_send_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_tool_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context -q`: 17 passed.
- `uv run --extra dev python -m pytest tests/services/wizard tests/services/upgrade/test_service.py tests/services/setup/test_state.py tests/services/setup/test_policy_inputs.py tests/cli/profile_cli/test_helpers.py tests/services/agent_loop/test_loop_llm.py::test_llm_visible_tools_include_explicitly_approved_tool_without_bypassing_deny_rules tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_passes_cli_policy_tool_approval_override tests/services/agent_loop/test_tool_execution_runtime.py::test_execute_requested_tool_calls_filters_channel_owned_generic_approval tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_tool_is_visible_in_minimal_channel tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_context_survives_agent_loop_resolution tests/services/agent_loop/test_loop_policy_gates.py::test_llm_active_channel_history_execution_receives_trusted_context tests/services/agent_loop/test_loop_policy_gates.py::test_active_channel_history_approval_requires_trusted_channel_context tests/cli/channels/test_wizard_copy.py tests/cli/channels/test_telegram_add.py tests/cli/channels/test_telethon_add.py tests/cli/channels/test_partyflow_add.py -q`: 86 passed.
- `uv run --extra dev ruff check afkbot tests`: passed.
- `uv run --extra dev mypy afkbot tests`: passed.
- `uv run --extra dev python -m pytest -q`: 2023 passed, 1 skipped, 2 existing `aiosqlite` thread warnings in `tests/cli/channels/test_telegram_add.py::test_channel_telegram_add_accepts_group_trigger_mode`.
- `git diff --check`: passed.

## Review Closure

- Spec review P1 fixed: profile add/update preview now infers real scenario and workspace scope, including `full_system` warnings.
- Spec review P2 fixed: channel wizard branch graph includes an explicit `trusted_admin` branch.
- Security review P1 fixed: empty legacy shell scopes migrate to `shell_sandbox_mode=required` because runtime treats them as profile-only restricted scopes.
- Security review P2 fixed: `channel.history.list` is filtered out of generic approval overrides and only receives channel-owned approval from trusted active-channel context.
