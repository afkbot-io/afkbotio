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

    manifest = PluginManifest.model_validate(
        {
            "plugin_id": plugin_id,
            "name": name,
            "version": version,
            "afkbot_version": "*",
            "kind": "embedded",
            "entrypoint": f"afkbot_plugin_{plugin_id}.plugin:register",
            "description": f"{name} for AFKBOT.",
            "default_config": {},
            "config_schema": {"fields": {}},
            "permissions": {
                "database": "none",
                "taskflow": "none",
                "outbound_http": False,
                "data_dir_write": False,
            },
            "capabilities": {
                "api_router": api_router,
                "static_web": static_web,
                "tools": tools,
                "skills": skills,
                "apps": apps,
                "lifecycle": lifecycle,
            },
            "mounts": {
                "api_prefix": f"/v1/plugins/{plugin_id}" if api_router else None,
                "web_prefix": f"/plugins/{plugin_id}" if static_web else None,
            },
            "paths": {
                "python_root": "python",
                "web_root": "web/dist" if static_web else None,
                "skills_root": "skills" if skills else None,
            },
        }
    )

    manifest_dir = plugin_root / ".afkbot-plugin"
    python_package_dir = plugin_root / "python" / f"afkbot_plugin_{plugin_id}"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    python_package_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = manifest_dir / "plugin.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (python_package_dir / "__init__.py").write_text("", encoding="utf-8")

    if api_router:
        _write_router_stub(plugin_root=plugin_root, plugin_id=plugin_id)
    if static_web:
        web_root = plugin_root / "web" / "dist"
        web_root.mkdir(parents=True, exist_ok=True)
        (web_root / "index.html").write_text(_index_html(plugin_id=plugin_id, name=name), encoding="utf-8")
    if skills:
        skills_root = plugin_root / "skills" / plugin_id
        skills_root.mkdir(parents=True, exist_ok=True)
        (skills_root / "SKILL.md").write_text(_skill_markdown(plugin_id=plugin_id, name=name), encoding="utf-8")

    entrypoint_path = python_package_dir / "plugin.py"
    entrypoint_path.write_text(
        _plugin_entrypoint(
            plugin_id=plugin_id,
            api_router=api_router,
            tools=tools,
            apps=apps,
            lifecycle=lifecycle,
        ),
        encoding="utf-8",
    )
    (plugin_root / "README.md").write_text(_readme(plugin_id=plugin_id, name=name), encoding="utf-8")

    return PluginScaffoldResult(
        plugin_root=plugin_root,
        manifest_path=manifest_path,
        entrypoint_path=entrypoint_path,
    )


def _write_router_stub(*, plugin_root: Path, plugin_id: str) -> None:
    router_path = plugin_root / "python" / f"afkbot_plugin_{plugin_id}" / "router.py"
    router_path.write_text(
        f'''"""FastAPI router for the {plugin_id} plugin."""\n\n'''
        'from __future__ import annotations\n\n'
        'from fastapi import APIRouter\n\n\n'
        'def build_router(*, api_prefix: str) -> APIRouter:\n'
        '    """Build one minimal plugin router."""\n\n'
        '    router = APIRouter(prefix=api_prefix, tags=["plugin"])\n\n'
        '    @router.get("/health")\n'
        '    async def health() -> dict[str, str]:\n'
        f'        return {{"status": "ok", "plugin": "{plugin_id}"}}\n\n'
        '    return router\n',
        encoding="utf-8",
    )


def _plugin_entrypoint(
    *,
    plugin_id: str,
    api_router: bool,
    tools: bool,
    apps: bool,
    lifecycle: bool,
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
                f"from afkbot_plugin_{plugin_id}.router import build_router",
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
    if not any((api_router, tools, apps, lifecycle)):
        lines.extend(
            [
                "",
                "    _ = registry",
            ]
        )
    lines.append("")
    return "\n".join(lines)


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


def _readme(*, plugin_id: str, name: str) -> str:
    return f"""# {name}

Embedded AFKBOT plugin scaffold.

Local install:

```bash
afk plugin install .
afk plugin config-get {plugin_id}
```

Entry points:

- Manifest: `.afkbot-plugin/plugin.json`
- Python: `python/afkbot_plugin_{plugin_id}/plugin.py`
- Web: `web/dist`
- Config: `afk plugin config-set {plugin_id} '{{}}'`
"""
