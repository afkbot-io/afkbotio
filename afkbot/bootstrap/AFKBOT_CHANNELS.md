# AFKBOT Channels

Channels are external transports that route user messages into an AFKBOT
profile/session and optionally deliver replies back through the same or another
transport. The active profile owns identity, memory, skills, subagents,
credentials, and tool permissions.

## Built-In Channels

First-class channel families today:

- Telegram Bot API polling:
  `transport=telegram`, `adapter_kind=telegram_bot_polling`.
- Telethon userbot:
  `transport=telegram_user`, `adapter_kind=telethon_userbot`.
- PartyFlow polling:
  `transport=partyflow`, `adapter_kind=partyflow_polling`.

Important files:

- Endpoint contracts: `afkbot/services/channels/endpoint_contracts.py`.
- Endpoint persistence: `afkbot/services/channels/endpoint_service.py`.
- Plugin adapter contract: `afkbot/services/channels/plugin_adapters.py`.
- Plugin inbound helper: `afkbot/services/channels/plugin_ingress.py`.
- Runtime startup: `afkbot/services/channels/runtime_manager.py`.
- Channel routing: `afkbot/services/channel_routing/*`.
- Active channel context: `afkbot/services/channels/active_context.py`.
- Prompt/tool-profile overrides: `afkbot/services/channels/context_overrides.py`,
  `afkbot/services/channels/tool_profiles.py`.
- Outbound delivery: `afkbot/services/channels/service.py`,
  `afkbot/services/channels/delivery_runtime.py`.
- Agent tool: `afkbot/services/tools/plugins/channel_send/plugin.py`.
- CLI surfaces: `afkbot/cli/commands/channel.py`,
  `afkbot/cli/commands/channel_telegram*`,
  `afkbot/cli/commands/channel_telethon*`,
  `afkbot/cli/commands/channel_partyflow.py`.

## Plugin Channel Surface

AFKBOT supports OpenClaw-style plugin channel adapters through the plugin runtime.
A plugin with `capabilities.channels=true` can register a
`ChannelAdapterFactory` in its entrypoint:

```python
def register(registry: PluginRuntimeRegistry) -> None:
    registry.register_channel_adapter(build_channel_adapter())
```

The factory is keyed by `(transport, adapter_kind)` and may provide:

- `build_runtime(settings, endpoint, state_dir)`: creates a service with
  `start()` and `stop()` for polling/webhook workers.
- `send_message(settings, target, message, credential_profile_key)`: sends
  outbound messages through the provider.
- `validate_target(target)`: normalizes provider-specific target fields before
  delivery.
- `outbound_target_key(target)`: returns the id checked against
  `access_policy.outbound_allow_to`.
- `validate_endpoint_config(endpoint)`: validates provider-specific endpoint
  config before the generic CLI persists it.
- `setup_instructions` and `endpoint_config_schema`: lightweight setup metadata
  shown by `afk channel plugin adapters --json`.

`endpoint_config_schema` is a primitive field map. Each field can declare:

- `type`: `string`, `integer`, `number`, or `boolean`.
- `default`: value applied when `afk channel plugin add --yes` or non-interactive
  setup omits `--config-json`.
- `required`: defaults to `true`; set `false` for optional provider knobs.
- `choices`: allowed string values.
- `minimum`/`maximum` for numeric fields.
- `min_length`/`max_length`/`pattern` for string fields.
- `secret`: hides interactive text entry; prefer AFKBOT credentials for actual
  provider tokens and passwords.

AFKBOT validates the schema when the plugin registers the adapter, validates
endpoint payloads in `afk channel plugin add`, and validates again before runtime
startup. Use `validate_endpoint_config(...)` for cross-field checks that a
primitive schema cannot express.

Use the scaffold when the agent needs to author a new channel:

```bash
afk plugin scaffold ./plugins/avito-channel \
  --plugin-id avito \
  --name "Avito Channel" \
  --channel
```

Create an endpoint for an installed adapter with:

```bash
afk channel plugin adapters
afk channel plugin add avito-main \
  --transport avito \
  --adapter-kind avito_polling \
  --profile default \
  --credential-profile avito-main \
  --account-id seller-1 \
  --allow-from buyer-1 \
  --outbound-allow-to conv-1 \
  --config-json '{"poll_interval_sec": 30}'
```

Operate a plugin channel endpoint with the generic channel commands:

```bash
afk channel show avito-main
afk channel disable avito-main
afk channel enable avito-main
afk channel delete avito-main
```

Operate the installed plugin itself with:

```bash
afk plugin inspect avito
afk plugin disable avito --force
afk plugin enable avito
afk plugin remove avito --delete-channel-endpoints
```

`afk plugin disable` and `afk plugin remove` block by default when configured
channel endpoints still depend on the plugin. Disable/delete the channel
endpoint first, pass `--force` to leave endpoints orphaned intentionally, or use
`--delete-channel-endpoints` during plugin removal.

`ChannelRuntimeManager`, `ChannelDeliveryService`, and `channel.send` resolve
registered plugin adapters after checking built-in adapters. Core still owns
profile binding, endpoint persistence, channel access policy, tool-profile
gating, and the shared `channel.send` policy path.

## OpenClaw-Style Access Controls

OpenClaw-style controls are represented by `ChannelAccessPolicy`:

- `private_policy`: `open`, `allowlist`, or `disabled`.
- `allow_from`: allowed private sender ids.
- `group_policy`: `open`, `allowlist`, or `disabled`.
- `groups`: allowed group/conversation ids.
- `group_allow_from`: allowed senders inside allowed groups.
- `outbound_allow_to`: allowed outbound peer/conversation ids for `channel.send`.

The shared CLI collector is
`collect_channel_access_policy_inputs(...)` in
`afkbot/cli/commands/channel_shared.py`. Binding helpers can create narrow
channel-routing rules from these allowlists instead of broad transport bindings.

For a private user-owned channel, default to allowlists. Keep inbound access,
outbound access, and profile tool permissions separately scoped.

## Custom Channel Reality Check

A user can ask for a "custom channel", but there are two different meanings:

1. Integration bridge: build an embedded plugin with API routes/static UI and
   call `/v1/chat/turn` with `transport`, `account_id`, `peer_id`, `thread_id`,
   `user_id`, and `resolve_binding` when an external service posts a message.
   This is viable for webhook/API bridge work, but it is not a managed channel
   runtime unless the plugin also registers a channel adapter.
2. First-class plugin channel: scaffold a plugin with `--channel`, implement its
   runtime/sender/target hooks, install it, and create a generic endpoint through
   `afk channel plugin add`.
3. Native built-in channel: modify core when the provider needs deep first-party
   UX, typed setup flows, or built-in maintenance guarantees.

Do not bypass the plugin manifest. Channel plugins require
`capabilities.channels=true`; runtime or sender hooks also require
`permissions.outbound_http=true` because they execute provider I/O. Runtime
workers require `permissions.data_dir_write=true` because core passes them an
endpoint-owned state directory. Plugin channels may not reuse core transports
such as `telegram`, `telegram_user`, `partyflow`, or `smtp`.

## What OpenClaw Does Differently

OpenClaw makes channels a plugin capability. A native plugin can register a
channel through the plugin API, and channel plugins are responsible for the
transport-specific pieces while core owns the shared messaging contract.

Useful OpenClaw design points to copy:

- Plugin entrypoints are declared by package metadata and loaded by the gateway.
- Channel plugins use a channel-specific entrypoint instead of a generic tool
  plugin entrypoint.
- Core keeps one shared outbound message tool; channel plugins do not each expose
  a separate send tool to the agent.
- The plugin owns account/config resolution, setup wizard metadata, DM security,
  allowlists, pairing, inbound normalization, outbound send functions, threading,
  and provider-specific conversation id parsing.
- Core owns common session shape, dispatch into the agent runtime, generic
  thread bookkeeping, approval lifecycle, and message-tool schema.
- Channel plugins can expose lightweight setup metadata separately from heavy
  runtime code so onboarding can inspect config without booting the channel.
- Webhook channels register an HTTP route; polling channels start a background
  service from the channel runtime.

AFKBOT now follows the same separation for Python plugins:
`register_channel_adapter(...)` is the plugin-owned registration point, while
core owns endpoint storage, profile/session policy, delivery policy, and the
shared outbound tool.

## Self-Authored Channel Goal

The user experience should be:

1. User asks: "connect Avito messages to AFKBOT".
2. Agent reads this bootstrap docs plus a channel plugin template.
3. Agent scaffolds a plugin, for example `plugins/avito-channel`.
4. Agent writes:
   - config schema for Avito credentials, polling interval, allowed users/chats,
     outbound allowlist, and optional listing filters;
   - a polling script/service that fetches new messages idempotently;
   - a send adapter that replies to an Avito conversation;
   - inbound normalization that maps Avito messages to AFKBOT channel events;
   - tests with fake Avito API responses;
   - a short plugin README / setup notes.
5. Operator reviews requested permissions and provides credentials through the
   credentials flow.
6. Agent installs/enables the plugin.
7. Agent lists active adapters with `afk channel plugin adapters` and creates a
   generic endpoint with `afk channel plugin add`.
8. AFKBOT starts the plugin channel runtime, routes inbound messages into the
   selected profile/session, and lets the agent reply through shared
   `channel.send`.

The important boundary is that the agent may author code, but activation should
still be explicit and reviewable. Installing self-authored channel code is local
code execution.

## AFKBOT Channel Plugin API

A minimal channel plugin registration contract looks like this in Python:

```python
from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory
from afkbot.services.channels.plugin_ingress import PluginChannelIngressDispatcher, PluginInboundMessage

def register(registry: PluginRuntimeRegistry) -> None:
    registry.register_channel_adapter(
        ChannelAdapterFactory(
            transport="avito",
            adapter_kind="avito_polling",
            build_runtime=lambda settings, endpoint, state_path: AvitoPollingService(
                settings=settings,
                endpoint=endpoint,
                state_path=state_path,
            ),
            send_message=send_avito_message,
            validate_endpoint_config=validate_avito_endpoint,
            validate_target=validate_avito_target,
            outbound_target_key=lambda target: target.address or target.peer_id,
            setup_instructions="Create Avito credentials, then create an endpoint.",
            endpoint_config_schema={
                "poll_interval_sec": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 3600,
                    "default": 30,
                },
            },
        )
    )
```

Polling runtimes should use the shared plugin ingress helper instead of calling
AgentLoop directly:

```python
class AvitoPollingService:
    def __init__(self, settings, endpoint, state_path):
        self._dispatcher = PluginChannelIngressDispatcher(settings, endpoint=endpoint)

    async def handle_provider_message(self, event):
        await self._dispatcher.dispatch_text(
            PluginInboundMessage(
                peer_id=event.conversation_id,
                thread_id=event.listing_id,
                user_id=event.sender_id,
                text=event.text,
                event_key=event.event_id,
            )
        )
```

The concrete names can change, but the responsibilities should stay separate:

- Generic endpoint config lives in `ChannelEndpointConfig.config`; plugin code
  should validate provider-specific keys before using them.
- Runtime service owns `start()` and `stop()`.
- Inbound adapter emits `PluginInboundMessage` and calls
  `PluginChannelIngressDispatcher.dispatch_text(...)`.
- Sender adapter handles outbound messages for `channel.send`.
- Setup/onboarding metadata lives on `ChannelAdapterFactory`; declare primitive
  endpoint fields in `endpoint_config_schema` so `afk channel plugin add` can
  fill defaults, prompt interactively, and validate `ChannelEndpointConfig.config`.
  Keep the plugin README in sync because agents read it after scaffolding.

## Remaining Design Work

- Webhook route helper that wires plugin HTTP routes directly into channel
  ingress.
- Media/attachment contracts for non-text outbound delivery.
- Rich provider-specific setup flows beyond primitive config fields; implement
  those as plugin API/UI surfaces or document them in the plugin README.

## Avito-Style Integration Shape

For a marketplace or mailbox-like service such as Avito, prefer polling first
unless the provider has reliable webhooks.

Runtime pieces:

- `AvitoPollingService.start()` creates one background task per endpoint.
- Poll loop reads a cursor from endpoint state, fetches conversations/messages,
  filters already-seen event ids, and persists the new cursor atomically.
- Each inbound message becomes an AFKBOT channel event with:
  `transport=avito`, `account_id`, `peer_id=<conversation_id>`,
  `user_id=<sender_id>`, optional `thread_id=<listing_id>`, and attachment
  metadata if supported.
- Access policy runs before dispatch: DM/group distinction may map to
  one-to-one buyer chats versus listing/team conversations.
- Session policy maps conversation id plus optional listing id into a stable
  AFKBOT session id.
- `AvitoSender.send(...)` takes the resolved target and text/media, calls Avito,
  and returns provider message ids.

Agent-authored plugin docs should require:

- no plaintext secrets in code, tests, logs, chat, or plugin README;
- bounded polling intervals and backoff;
- idempotency keys for inbound events;
- rate-limit handling;
- clear unsupported-media behavior;
- outbound allowlist defaults for private integrations;
- dry-run/fake-client tests before install.

## Adding A First-Class Channel

Use this checklist when implementing native support:

1. Add a typed endpoint model in `endpoint_contracts.py` with transport,
   adapter_kind, adapter-specific config, validation, and `storage_config()`.
2. Update `serialize_endpoint_storage_payload(...)` and
   `deserialize_endpoint_config(...)`.
3. Implement a runtime service under `afkbot/services/channels/<transport>/` or a
   dedicated module. It must expose `start()` and `stop()`, process inbound
   events idempotently, build trusted channel context, and call the agent loop.
4. Update `ChannelRuntimeManager` to construct the service for the new
   transport/adapter pair.
5. Register any live outbound sender in `ChannelSenderRegistry` if replies are
   delivered by a long-lived client instead of app actions.
6. Extend delivery target validation and `ChannelDeliveryService` if
   `channel.send` or final-turn delivery should support this transport.
7. Extend `ChannelSendTool` supported transports, parameters, policy checks, and
   error messages.
8. Add CLI commands under `afk channel <name>` or expose an operator plugin/API
   surface that creates endpoint configs safely.
9. Add access-policy prompts using `collect_channel_access_policy_inputs(...)`
   and avoid broad default bindings for user-facing channels.
10. Add tests for endpoint persistence, runtime start/stop, inbound routing,
    outbound policy, `channel.send`, CLI add/show/status/delete, and unsafe
    allowlist cases.

## Integration Bridge Pattern

When a user wants a custom external service quickly and does not require native
`channel.send`, prefer a plugin bridge:

1. Scaffold a plugin with `--api-router` and optional `--static-web`.
2. Add a signed webhook route or authenticated API route inside the plugin.
3. Normalize external events into text plus stable routing coordinates.
4. Call the authenticated Chat API turn path with a profile/session scope and
   optional channel routing fields.
5. Store plugin-specific webhook secrets in credentials or plugin config; never
   in chat, logs, or static assets.
6. Return the assistant response through the plugin's own external API client if
   the transport is not supported by core `ChannelDeliveryService`.

This pattern is useful for prototypes and private customer integrations. Promote
it to a first-class channel only when the transport needs shared CLI setup,
operator status, managed polling/watchers, `channel.send`, access policy, and
runtime lifecycle parity with the built-in channels.
