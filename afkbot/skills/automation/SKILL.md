---
name: automation
description: "Automation management via `automation.*` tools. Use when the user explicitly wants to create, inspect, update, list, or delete cron or webhook automations."
aliases: automation-cron, automation-webhook, automation-list, automation-get, automation-edit, automation-delete, cron, webhook
triggers:
  - cron
  - schedule
  - scheduled
  - webhook
  - automation
  - automate
  - create cron
  - create webhook
  - создай крон
  - создай крон
  - создай cron
  - настрой cron
  - автоматизацию
  - автоматизация
  - расписание
  - по расписанию
tool_names:
  - automation.list
  - automation.get
  - automation.create
  - automation.update
  - automation.delete
preferred_tool_order:
  - automation.create
  - automation.update
  - automation.get
  - automation.list
  - automation.delete
execution_mode: executable
---
# automation

Manage automation entities via `automation.*` tools.

Use this skill only for explicit automation requests:
- schedule/cron jobs
- webhook automations
- automation list/get/update/delete

## Workflow
1. Clarify target operation (`create`, `list`, `get`, `update`, `delete`).
2. For `create`, collect required fields:
   - `name`
   - `prompt`
   - `trigger_type` (`cron` or `webhook`)
   - if `cron`: `cron_expr` (+ optional `timezone`, default `UTC`)
   - first think in terms of the final outcome the automation must produce on each run:
     - what exactly should happen
     - what external effect is required, if any
     - where the result should go
   - ask follow-up questions only when the final outcome is blocked on missing required data
   - rewrite the user's ask into a self-contained automation `prompt` that states the task, desired outcome, and any constraints; do not include schedule details in the stored prompt
   - the rewritten prompt should be explicit enough that a later automation run can understand it without the original chat context
   - choose `delivery_mode`:
     - `target` only when the user gave an exact destination that AFKBOT can validate now
     - `tool` when the automation should use tools like `app.run`, `http.request`, or `bash.exec` during execution
     - `none` when no outbound delivery is needed
   - if request says “send via Telegram/email/etc.” without an exact target, prefer `delivery_mode=tool` and encode the tool-driven send into the automation prompt
   - when using `delivery_mode=tool`, prefer existing configured integration defaults when they are likely available:
     - Telegram bot app can use the configured `telegram_chat_id` credential if `chat_id` is omitted
     - SMTP can use configured sender defaults when only the recipient is variable
   - if no safe default target is likely and the destination is required, ask one concise follow-up question for the exact destination
   - for `delivery_target`, use AFKBOT delivery coordinates (`binding_id`, `account_id`, `peer_id`, `thread_id`, `user_id`, `address`) instead of app-specific payload keys
   - for Telegram bot delivery, prefer `peer_id` (or `chat_id`/`address` as compatibility aliases); do not invent `user_id` for a Telegram bot target
3. For `update`, require `id` and include only changed fields.
4. For `delete`, require `id` and restate target before execution.
5. After tool call, return short factual result from payload:
   - `id`, `name`, `status`, `trigger_type`
   - for cron: `cron.cron_expr`, `cron.timezone`
   - for webhook create/rotate: `webhook.webhook_path`, `webhook.webhook_token`, and `webhook.webhook_token_masked`
   - explain that external webhook providers should call the returned `webhook.webhook_path` on the AFKBOT base URL

## Rules
- Never call `automation.*` for plain integration tasks (e.g. send Telegram message once).
- For requests that mix automation + integration (e.g. “create cron that sends Telegram message”), automation owns the current turn. Use `automation.create` or `automation.update` now; the referenced channel/app skill is only input to the automation prompt.
- Do not invent `delivery_target` when the user did not give an explicit destination. If the request only says “send via Telegram/email”, encode that in the automation `prompt`, set `delivery_mode=tool`, and leave `delivery_target` unset.
- In `delivery_mode=tool`, prefer prompts that name the expected tool path explicitly, for example:
  - `Use app.run with the telegram app to send ...`
  - `Use http.request to POST ...`
  - `Use bash.exec to run ...`
- Do not invent missing required fields when user can provide them.
- Never claim success without successful `automation.*` tool result in current turn.
