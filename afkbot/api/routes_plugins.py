"""HTTP routes for inspecting installed AFKBOT plugins."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from afkbot.services.plugins import PluginServiceError, get_plugin_service
from afkbot.settings import get_settings

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])


class PluginConfigPayload(BaseModel):
    """HTTP request payload for replacing one plugin config."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, object] = Field(default_factory=dict)


@router.get("")
async def list_plugins() -> dict[str, object]:
    """List installed plugins with current runtime surface summary."""

    settings = get_settings()
    service = get_plugin_service(settings)
    installed = {item.plugin_id: item for item in service.list_installed()}
    runtime = service.load_runtime_snapshot()
    payload = []
    for plugin in installed.values():
        config = service.get_config(plugin_id=plugin.plugin_id)
        runtime_plugin = next((item for item in runtime.plugins if item.record.plugin_id == plugin.plugin_id), None)
        payload.append(
            {
                "plugin": plugin.model_dump(mode="json"),
                "config": {
                    "source": config.source,
                    "config_path": config.config_path,
                    "data_dir": config.data_dir,
                    "keys": sorted(config.config.keys()),
                    "schema_keys": sorted(config.config_schema.fields.keys()),
                },
                "runtime": {
                    "active": runtime_plugin is not None,
                    "router_count": len(runtime_plugin.routers) if runtime_plugin is not None else 0,
                    "static_mount_count": len(runtime_plugin.static_mounts) if runtime_plugin is not None else 0,
                    "skill_dir_count": len(runtime_plugin.skill_dirs) if runtime_plugin is not None else 0,
                    "tool_factory_count": len(runtime_plugin.tool_factories) if runtime_plugin is not None else 0,
                    "app_registrar_count": len(runtime_plugin.app_registrars) if runtime_plugin is not None else 0,
                    "startup_hook_count": len(runtime_plugin.startup_hooks) if runtime_plugin is not None else 0,
                    "shutdown_hook_count": len(runtime_plugin.shutdown_hooks) if runtime_plugin is not None else 0,
                },
            }
        )
    return {"plugins": payload}


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str) -> dict[str, object]:
    """Return one installed plugin record plus runtime surface summary."""

    settings = get_settings()
    service = get_plugin_service(settings)
    try:
        plugin = service.inspect(plugin_id=plugin_id)
        config = service.get_config(plugin_id=plugin_id)
    except PluginServiceError as exc:
        status_code = 404 if exc.error_code == "plugin_not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": exc.error_code, "reason": exc.reason},
        ) from exc
    runtime = service.load_runtime_snapshot()
    runtime_plugin = next((item for item in runtime.plugins if item.record.plugin_id == plugin.plugin_id), None)
    return {
        "plugin": plugin.model_dump(mode="json"),
        "config": {
            "source": config.source,
            "config_path": config.config_path,
            "data_dir": config.data_dir,
            "config_schema": config.config_schema.model_dump(mode="json"),
            "config": config.config,
        },
        "runtime": {
            "active": runtime_plugin is not None,
            "router_count": len(runtime_plugin.routers) if runtime_plugin is not None else 0,
            "static_mount_count": len(runtime_plugin.static_mounts) if runtime_plugin is not None else 0,
            "skill_dir_count": len(runtime_plugin.skill_dirs) if runtime_plugin is not None else 0,
            "tool_factory_count": len(runtime_plugin.tool_factories) if runtime_plugin is not None else 0,
            "app_registrar_count": len(runtime_plugin.app_registrars) if runtime_plugin is not None else 0,
            "startup_hook_count": len(runtime_plugin.startup_hooks) if runtime_plugin is not None else 0,
            "shutdown_hook_count": len(runtime_plugin.shutdown_hooks) if runtime_plugin is not None else 0,
        },
    }


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, object]:
    """Return one plugin config and resolved storage paths."""

    settings = get_settings()
    service = get_plugin_service(settings)
    try:
        config = service.get_config(plugin_id=plugin_id)
    except PluginServiceError as exc:
        status_code = 404 if exc.error_code == "plugin_not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": exc.error_code, "reason": exc.reason},
        ) from exc
    return {"plugin_config": config.model_dump(mode="json")}


@router.put("/{plugin_id}/config")
async def put_plugin_config(plugin_id: str, payload: PluginConfigPayload) -> dict[str, object]:
    """Replace one plugin config JSON object."""

    settings = get_settings()
    service = get_plugin_service(settings)
    try:
        config = service.set_config(plugin_id=plugin_id, config=payload.config)
    except PluginServiceError as exc:
        status_code = 404 if exc.error_code == "plugin_not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": exc.error_code, "reason": exc.reason},
        ) from exc
    return {"plugin_config": config.model_dump(mode="json")}


@router.delete("/{plugin_id}/config")
async def delete_plugin_config(plugin_id: str) -> dict[str, object]:
    """Reset one plugin config back to manifest defaults."""

    settings = get_settings()
    service = get_plugin_service(settings)
    try:
        config = service.reset_config(plugin_id=plugin_id)
    except PluginServiceError as exc:
        status_code = 404 if exc.error_code == "plugin_not_found" else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error_code": exc.error_code, "reason": exc.reason},
        ) from exc
    return {"plugin_config": config.model_dump(mode="json")}
