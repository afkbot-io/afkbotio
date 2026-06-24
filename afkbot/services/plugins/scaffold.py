"""Scaffold helpers for embedded AFKBOT plugins."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from afkbot.services.plugins.contracts import PluginManifest, PluginServiceError


@dataclass(frozen=True, slots=True)
class PluginScaffoldResult:
    """Metadata describing one generated plugin scaffold."""

    plugin_root: Path
    manifest_path: Path
    entrypoint_path: Path


def scaffold_plugin(
    *,
    destination: Path,
    plugin_id: str,
    name: str,
    version: str = "0.1.0",
    api_router: bool = True,
    static_web: bool = True,
    skills: bool = False,
    tools: bool = False,
    apps: bool = False,
    lifecycle: bool = False,
    channel: bool = False,
    force: bool = False,
) -> PluginScaffoldResult:
    """Create one embedded AFKBOT plugin skeleton on disk."""

    plugin_root = destination.expanduser().resolve(strict=False)
    if plugin_root.exists() and any(plugin_root.iterdir()) and not force:
        raise PluginServiceError(
            error_code="plugin_scaffold_exists",
            reason=f"Destination already exists and is not empty: {plugin_root}",
        )
    plugin_root.mkdir(parents=True, exist_ok=True)
    normalized_plugin_id = plugin_id.strip().lower()
    package_name = f"afkbot_plugin_{normalized_plugin_id.replace('-', '_')}"

    manifest = PluginManifest.model_validate(
        {
            "plugin_id": plugin_id,
            "name": name,
            "version": version,
            "afkbot_version": "*",
            "kind": "embedded",
            "entrypoint": f"{package_name}.plugin:register",
            "description": f"{name} for AFKBOT.",
            "default_config": {},
            "config_schema": {"fields": {}},
            "permissions": {
                "database": "none",
                "taskflow": "none",
                "outbound_http": channel,
                "data_dir_write": channel,
            },
            "auth": {
                "operator_required": False,
                "public": False,
            },
            "capabilities": {
                "api_router": api_router,
                "static_web": static_web,
                "tools": tools,
                "skills": skills,
                "apps": apps,
                "lifecycle": lifecycle,
                "channels": channel,
            },
            "mounts": {
                "api_prefix": f"/v1/plugins/{normalized_plugin_id}" if api_router else None,
                "web_prefix": f"/plugins/{normalized_plugin_id}" if static_web else None,
            },
            "paths": {
                "python_root": "python",
                "web_root": "web/dist" if static_web else None,
                "skills_root": "skills" if skills else None,
            },
        }
    )

    manifest_dir = plugin_root / ".afkbot-plugin"
    python_package_dir = plugin_root / "python" / package_name
    manifest_dir.mkdir(parents=True, exist_ok=True)
    python_package_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = manifest_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (python_package_dir / "__init__.py").write_text("", encoding="utf-8")

    if api_router:
        _write_router_stub(
            plugin_root=plugin_root,
            plugin_id=manifest.plugin_id,
            package_name=package_name,
        )
    if static_web:
        web_root = plugin_root / "web" / "dist"
        web_root.mkdir(parents=True, exist_ok=True)
        (web_root / "index.html").write_text(
            _index_html(plugin_id=plugin_id, name=name), encoding="utf-8"
        )
    if skills:
        skills_root = plugin_root / "skills" / manifest.plugin_id
        skills_root.mkdir(parents=True, exist_ok=True)
        (skills_root / "SKILL.md").write_text(
            _skill_markdown(plugin_id=manifest.plugin_id, name=name),
            encoding="utf-8",
        )
    if channel:
        _write_channel_stub(
            plugin_root=plugin_root,
            plugin_id=manifest.plugin_id,
            package_name=package_name,
        )
        docs_root = plugin_root / "docs"
        docs_root.mkdir(parents=True, exist_ok=True)
        (docs_root / "CHANNEL_INTEGRATION_SPEC.md").write_text(
            _channel_integration_spec(plugin_id=manifest.plugin_id, package_name=package_name),
            encoding="utf-8",
        )

    entrypoint_path = python_package_dir / "plugin.py"
    entrypoint_path.write_text(
        _plugin_entrypoint(
            plugin_id=manifest.plugin_id,
            package_name=package_name,
            api_router=api_router,
            tools=tools,
            apps=apps,
            lifecycle=lifecycle,
            channel=channel,
        ),
        encoding="utf-8",
    )
    (plugin_root / "README.md").write_text(
        _readme(plugin_id=manifest.plugin_id, package_name=package_name, name=name, channel=channel),
        encoding="utf-8",
    )

    return PluginScaffoldResult(
        plugin_root=plugin_root,
        manifest_path=manifest_path,
        entrypoint_path=entrypoint_path,
    )


def _write_router_stub(*, plugin_root: Path, plugin_id: str, package_name: str) -> None:
    router_path = plugin_root / "python" / package_name / "router.py"
    router_path.write_text(
        f'''"""FastAPI router for the {plugin_id} plugin."""\n\n'''
        "from __future__ import annotations\n\n"
        "from fastapi import APIRouter\n\n\n"
        "def build_router(*, api_prefix: str) -> APIRouter:\n"
        '    """Build one minimal plugin router."""\n\n'
        '    router = APIRouter(prefix=api_prefix, tags=["plugin"])\n\n'
        '    @router.get("/health")\n'
        "    async def health() -> dict[str, str]:\n"
        f'        return {{"status": "ok", "plugin": "{plugin_id}"}}\n\n'
        "    return router\n",
        encoding="utf-8",
    )


def _plugin_entrypoint(
    *,
    plugin_id: str,
    package_name: str,
    api_router: bool,
    tools: bool,
    apps: bool,
    lifecycle: bool,
    channel: bool,
) -> str:
    lines = [
        f'"""Plugin entrypoint for the {plugin_id} AFKBOT plugin."""',
        "",
        "from __future__ import annotations",
        "",
        "from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry",
    ]
    if api_router:
        lines.extend(
            [
                "",
                f"from {package_name}.router import build_router",
            ]
        )
    if channel:
        lines.extend(
            [
                "",
                f"from {package_name}.channel import build_channel_adapter",
            ]
        )
    lines.extend(
        [
            "",
            "",
            "def register(registry: PluginRuntimeRegistry) -> None:",
            '    """Register plugin runtime surfaces."""',
            "    # Use registry.read_config() for validated config and registry.data_dir for writable plugin state.",
        ]
    )
    if api_router:
        lines.extend(
            [
                "",
                f'    api_prefix = registry.manifest.mounts.api_prefix or "/v1/plugins/{plugin_id}"',
                "    registry.register_router(build_router(api_prefix=api_prefix))",
                "    _ = registry.read_config()",
                "    _ = registry.data_dir",
            ]
        )
    if tools:
        lines.extend(
            [
                "",
                "    # registry.register_tool_factory(...)",
            ]
        )
    if apps:
        lines.extend(
            [
                "",
                "    # registry.register_app_registrar(...)",
            ]
        )
    if lifecycle:
        lines.extend(
            [
                "",
                "    async def _startup(settings) -> None:",
                "        _ = settings",
                "        _ = registry.read_config()",
                "        _ = registry.data_dir",
                "",
                "    async def _shutdown(settings) -> None:",
                "        _ = settings",
                "",
                "    registry.register_startup_hook(_startup)",
                "    registry.register_shutdown_hook(_shutdown)",
            ]
        )
    if channel:
        lines.extend(
            [
                "",
                "    registry.register_channel_adapter(build_channel_adapter())",
            ]
        )
    if not any((api_router, tools, apps, lifecycle, channel)):
        lines.extend(
            [
                "",
                "    _ = registry",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _write_channel_stub(*, plugin_root: Path, plugin_id: str, package_name: str) -> None:
    channel_path = plugin_root / "python" / package_name / "channel.py"
    channel_path.write_text(
        f'''"""Channel adapter scaffold for the {plugin_id} plugin."""\n\n'''
        "from __future__ import annotations\n\n"
        "import asyncio\n"
        "from pathlib import Path\n\n"
        "from afkbot.services.channels.contracts import ChannelDeliveryTarget, ChannelOutboundMessage\n"
        "from afkbot.services.channels.delivery_runtime import ResolvedDeliveryTarget\n"
        "from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig\n"
        "from afkbot.services.channels.plugin_ingress import PluginChannelIngressDispatcher, PluginInboundMessage\n"
        "from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory\n"
        "from afkbot.settings import Settings\n\n\n"
        "class PluginChannelRuntime:\n"
        "    \"\"\"Replace this with provider polling or webhook runtime.\"\"\"\n\n"
        "    def __init__(self, settings: Settings, endpoint: ChannelEndpointConfig, state_dir: Path) -> None:\n"
        "        self._settings = settings\n"
        "        self._endpoint = endpoint\n"
        "        self._state_dir = state_dir\n"
        "        self._dispatcher = PluginChannelIngressDispatcher(settings, endpoint=endpoint)\n"
        "        self._task: asyncio.Task[None] | None = None\n"
        "        self._stop = asyncio.Event()\n\n"
        "    async def start(self) -> None:\n"
        "        self._state_dir.mkdir(parents=True, exist_ok=True)\n"
        "        self._task = asyncio.create_task(self._run(), name=f\"{plugin_id}-channel-runtime\")\n\n"
        "    async def stop(self) -> None:\n"
        "        self._stop.set()\n"
        "        if self._task is not None:\n"
        "            self._task.cancel()\n"
        "            try:\n"
        "                await self._task\n"
        "            except asyncio.CancelledError:\n"
        "                pass\n\n"
        "    async def _run(self) -> None:\n"
        "        # Poll the provider, map each event into PluginInboundMessage, then dispatch it.\n"
        "        # await self._dispatcher.dispatch_text(\n"
        "        #     PluginInboundMessage(peer_id=\"provider-conversation-id\", text=\"hello\", event_key=\"provider-event-id\")\n"
        "        # )\n"
        "        await self._stop.wait()\n\n\n"
        "def validate_endpoint_config(endpoint: ChannelEndpointConfig) -> ChannelEndpointConfig:\n"
        "    poll_interval = endpoint.config.get(\"poll_interval_sec\", 30)\n"
        "    if not isinstance(poll_interval, int) or poll_interval < 5 or poll_interval > 3600:\n"
        "        raise ValueError(\"config.poll_interval_sec must be an integer between 5 and 3600\")\n"
        "    return endpoint\n\n\n"
        "def validate_target(target: ChannelDeliveryTarget) -> ChannelDeliveryTarget:\n"
        "    if not (target.peer_id or target.address):\n"
        "        raise ValueError(\"Channel target requires peer_id or address\")\n"
        "    return target\n\n\n"
        "def outbound_target_key(target: ResolvedDeliveryTarget) -> str | None:\n"
        "    return target.peer_id or target.address or target.user_id\n\n\n"
        "async def send_message(\n"
        "    settings: Settings,\n"
        "    target: ResolvedDeliveryTarget,\n"
        "    message: ChannelOutboundMessage,\n"
        "    credential_profile_key: str | None,\n"
        ") -> dict[str, object]:\n"
        "    _ = settings, credential_profile_key\n"
        "    # Call the provider API with endpoint credentials and return provider ids.\n"
        "    return {\n"
        f'        "transport": "{plugin_id}",\n'
        "        \"target\": target.to_payload(),\n"
        "        \"text_length\": len(message.text),\n"
        "    }\n\n\n"
        "def build_channel_adapter() -> ChannelAdapterFactory:\n"
        "    return ChannelAdapterFactory(\n"
        f'        transport="{plugin_id}",\n'
        f'        adapter_kind="{plugin_id}_polling",\n'
        f'        label="{plugin_id} channel",\n'
        "        description=\"Provider channel scaffold generated by AFKBOT.\",\n"
        "        setup_instructions=(\n"
        "            \"Configure provider credentials in AFKBOT credentials, then create an endpoint with \"\n"
        f"            \"afk channel plugin add <channel-id> --transport {plugin_id} --adapter-kind {plugin_id}_polling.\"\n"
        "        ),\n"
        "        endpoint_config_schema={\n"
        "            \"poll_interval_sec\": {\n"
        "                \"type\": \"integer\",\n"
        "                \"minimum\": 5,\n"
        "                \"maximum\": 3600,\n"
        "                \"default\": 30,\n"
        "                \"description\": \"Provider polling interval in seconds.\",\n"
        "            },\n"
        "        },\n"
        "        build_runtime=lambda settings, endpoint, state_dir: PluginChannelRuntime(\n"
        "            settings=settings,\n"
        "            endpoint=endpoint,\n"
        "            state_dir=state_dir,\n"
        "        ),\n"
        "        send_message=send_message,\n"
        "        validate_endpoint_config=validate_endpoint_config,\n"
        "        validate_target=validate_target,\n"
        "        outbound_target_key=outbound_target_key,\n"
        "    )\n",
        encoding="utf-8",
    )


def _index_html(*, plugin_id: str, name: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{name}</title>
  </head>
  <body>
    <main>
      <h1>{name}</h1>
      <p>Plugin id: {plugin_id}</p>
      <p>This is a starter static surface for an embedded AFKBOT plugin.</p>
    </main>
  </body>
</html>
"""


def _skill_markdown(*, plugin_id: str, name: str) -> str:
    return f"""---
name: {plugin_id}
description: {name} plugin skill.
---

# {name}

Describe when this plugin-provided skill should be used.
"""


def _channel_integration_spec(*, plugin_id: str, package_name: str) -> str:
    return f"""# Channel Integration Spec

This file is for agents extending this AFKBOT plugin into a real provider
channel. Read it before editing `python/{package_name}/channel.py`.

## Provider Contract

Fill these in before implementation:

- Provider name:
- Provider API documentation:
- Auth method and AFKBOT credential profile keys:
- Inbound source: polling, webhook, or both:
- Outbound send endpoint:
- Provider conversation id field:
- Provider sender/user id field:
- Provider message/event id field:
- Rate limits and retry semantics:
- Required endpoint config fields:

## AFKBOT Contract

- Keep `transport="{plugin_id}"`.
- Keep `adapter_kind="{plugin_id}_polling"` unless adding a new adapter variant.
- Put provider knobs in `ChannelEndpointConfig.config`.
- Declare primitive endpoint fields in `endpoint_config_schema`.
- Use AFKBOT credentials for tokens, passwords, cookies, and API keys.
- Persist cursors and dedupe state only under the endpoint `state_dir`.
- Normalize every inbound event into `PluginInboundMessage`.
- Use stable non-empty `peer_id` and `event_key`.
- Use `PluginChannelIngressDispatcher.dispatch_text(...)`; do not call AgentLoop
  directly from provider code.
- Implement `send_message(...)` for shared `channel.send` and same-conversation
  replies.
- Implement `outbound_target_key(...)` so `access_policy.outbound_allow_to`
  gates proactive sends.

## Runtime Flow

1. `afk plugin install . --overwrite --enable`
2. `afk channel plugin adapters`
3. `afk channel plugin add {plugin_id}-main --transport {plugin_id} --adapter-kind {plugin_id}_polling --credential-profile {plugin_id}-main --allow-from <provider-user-id> --outbound-allow-to <provider-conversation-id> --yes`
4. AFKBOT starts the plugin runtime with the endpoint config and a writable
   endpoint state directory.
5. The runtime polls provider events, dispatches normalized inbound messages,
   and stores provider cursors after successful dispatch.
6. AFKBOT replies through `send_message(...)` when the turn finalizes and reply
   policy allows it.

## Tests To Add

- Endpoint config schema defaults and validation.
- Runtime startup uses only endpoint `state_dir`.
- Polling maps provider event ids to non-empty `event_key`.
- Duplicate provider events are not processed twice.
- Access policy rejects disallowed users/groups.
- `send_message(...)` validates provider target ids and returns provider ids.
- `channel.send` outbound allowlist blocks unknown targets.
- Plugin disable/remove/update behavior when endpoints depend on this adapter.

## Security Checklist

- No secrets in code, tests, README, logs, or error messages.
- No writes outside plugin root or endpoint state dir.
- No reserved transports: telegram, telegram_user, partyflow, smtp.
- No broad default access for private customer/provider accounts.
- Network calls must respect provider rate limits and timeouts.
- Provider API failures must not mark cursors as processed until the inbound turn
  is safely handled.
"""


def _readme(*, plugin_id: str, package_name: str, name: str, channel: bool) -> str:
    channel_section = ""
    if channel:
        channel_section = f"""

## Channel Integration Workflow

This plugin was generated as an AFKBOT channel adapter. When an agent is asked
to complete the integration, it should:

1. Read `afkbot/bootstrap/AFKBOT.md`, `afkbot/bootstrap/AFKBOT_PLUGINS.md`, and
   `afkbot/bootstrap/AFKBOT_CHANNELS.md` in the AFKBOT repo.
2. Fill `docs/CHANNEL_INTEGRATION_SPEC.md` with the provider contract.
3. Implement provider polling in `python/{package_name}/channel.py`.
4. Map inbound provider events to `PluginInboundMessage` and call
   `PluginChannelIngressDispatcher.dispatch_text(...)`.
5. Implement `send_message(...)` with the provider API.
6. Tighten `validate_endpoint_config(...)`, `validate_target(...)`, and
   `outbound_target_key(...)` for provider-specific ids.
7. Add focused tests for runtime startup, outbound send, endpoint validation,
   and `channel.send` policy.
8. Install and enable locally:

```bash
afk plugin install . --overwrite --enable
afk channel plugin adapters
afk channel plugin add {plugin_id}-main \\
  --transport {plugin_id} \\
  --adapter-kind {plugin_id}_polling \\
  --credential-profile {plugin_id}-main \\
  --allow-from provider-user-id \\
  --outbound-allow-to provider-conversation-id \\
  --yes
```

Operate it later:

```bash
afk channel disable {plugin_id}-main
afk channel enable {plugin_id}-main
afk channel delete {plugin_id}-main
afk plugin disable {plugin_id}
afk plugin remove {plugin_id}
```
"""
    return f"""# {name}

Embedded AFKBOT plugin scaffold.

Local install:

```bash
afk plugin install .
afk plugin config-get {plugin_id}
```

Entry points:

- Manifest: `.afkbot-plugin/plugin.json`
- Python: `python/{package_name}/plugin.py`
- Web: `web/dist`
- Config: `afk plugin config-set {plugin_id} '{{}}'`
{channel_section}"""
