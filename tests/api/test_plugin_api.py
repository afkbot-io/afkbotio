"""API tests for mounted embedded plugins."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.plugins import get_plugin_service
from afkbot.settings import get_settings


def _write_api_demo_plugin(root: Path) -> None:
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    (root / "python/afkbot_plugin_demo").mkdir(parents=True, exist_ok=True)
    (root / "web/dist").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "demo",
                "name": "Demo Plugin",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": "afkbot_plugin_demo.plugin:register",
                "default_config": {"theme": "neon", "refresh_sec": 5},
                "config_schema": {
                    "fields": {
                        "theme": {"type": "string", "choices": ["neon", "midnight"]},
                        "refresh_sec": {"type": "integer", "minimum": 1, "maximum": 60},
                    },
                },
                "permissions": {
                    "database": "read_write",
                    "taskflow": "read_write",
                    "outbound_http": False,
                    "data_dir_write": False,
                },
                "capabilities": {"api_router": True, "static_web": True, "lifecycle": True},
                "mounts": {"api_prefix": "/v1/plugins/demo", "web_prefix": "/plugins/demo"},
                "paths": {"python_root": "python", "web_root": "web/dist"},
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "python/afkbot_plugin_demo/__init__.py").write_text("", encoding="utf-8")
    (root / "python/afkbot_plugin_demo/plugin.py").write_text(
        """
from __future__ import annotations

from fastapi import APIRouter

from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry


def register(registry: PluginRuntimeRegistry) -> None:
    router = APIRouter(prefix=registry.manifest.mounts.api_prefix or "/v1/plugins/demo")

    @router.get("/ping")
    async def ping():
        return {"plugin": "demo"}

    async def _startup(settings):
        (settings.root_dir / "plugin-started.txt").write_text("started", encoding="utf-8")

    async def _shutdown(settings):
        (settings.root_dir / "plugin-stopped.txt").write_text("stopped", encoding="utf-8")

    registry.register_router(router)
    registry.register_startup_hook(_startup)
    registry.register_shutdown_hook(_shutdown)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "web/dist/index.html").write_text("<html><body>demo mounted</body></html>\n", encoding="utf-8")


def _write_failing_lifecycle_plugin(root: Path) -> None:
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    (root / "python/afkbot_plugin_faildemo").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "faildemo",
                "name": "Fail Demo Plugin",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": "afkbot_plugin_faildemo.plugin:register",
                "capabilities": {"api_router": False, "static_web": False, "lifecycle": True},
                "mounts": {},
                "paths": {"python_root": "python"},
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "python/afkbot_plugin_faildemo/__init__.py").write_text("", encoding="utf-8")
    (root / "python/afkbot_plugin_faildemo/plugin.py").write_text(
        """
from __future__ import annotations

from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry


def register(registry: PluginRuntimeRegistry) -> None:
    async def _startup(settings):
        (settings.root_dir / "plugin-started.txt").write_text("started", encoding="utf-8")
        raise RuntimeError("boom")

    async def _shutdown(settings):
        marker = settings.root_dir / "plugin-stopped.txt"
        count = int(marker.read_text(encoding="utf-8")) if marker.exists() else 0
        marker.write_text(str(count + 1), encoding="utf-8")

    registry.register_startup_hook(_startup)
    registry.register_shutdown_hook(_shutdown)
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_create_app_mounts_installed_plugin(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_api_demo_plugin(source_root)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with TestClient(create_app()) as client:
        plugins_response = client.get("/v1/plugins")
        config_response = client.get("/v1/plugins/demo/config")
        api_response = client.get("/v1/plugins/demo/ping")
        plugin_response = client.get("/v1/plugins/demo")
        web_response = client.get("/plugins/demo/")

        assert plugins_response.status_code == 200
        plugins_payload = plugins_response.json()["plugins"]
        assert len(plugins_payload) == 1
        assert plugins_payload[0]["plugin"]["plugin_id"] == "demo"
        assert plugins_payload[0]["plugin"]["manifest"]["permissions"]["taskflow"] == "read_write"
        assert plugins_payload[0]["config"]["source"] == "default"
        assert plugins_payload[0]["config"]["keys"] == ["refresh_sec", "theme"]
        assert plugins_payload[0]["config"]["schema_keys"] == ["refresh_sec", "theme"]
        assert plugins_payload[0]["runtime"]["active"] is True
        assert plugins_payload[0]["runtime"]["startup_hook_count"] == 1
        assert config_response.status_code == 200
        assert config_response.json()["plugin_config"]["source"] == "default"
        assert config_response.json()["plugin_config"]["config"] == {"theme": "neon", "refresh_sec": 5}
        assert config_response.json()["plugin_config"]["config_schema"]["fields"]["refresh_sec"]["type"] == "integer"
        set_config_response = client.put("/v1/plugins/demo/config", json={"config": {"theme": "midnight"}})
        assert set_config_response.status_code == 200
        assert set_config_response.json()["plugin_config"]["source"] == "persisted"
        assert set_config_response.json()["plugin_config"]["config"] == {"theme": "midnight", "refresh_sec": 5}
        invalid_config_response = client.put("/v1/plugins/demo/config", json={"config": {"theme": "midnight", "oops": True}})
        assert invalid_config_response.status_code == 400
        reset_config_response = client.delete("/v1/plugins/demo/config")
        assert reset_config_response.status_code == 200
        assert reset_config_response.json()["plugin_config"]["source"] == "default"
        assert reset_config_response.json()["plugin_config"]["config"] == {"theme": "neon", "refresh_sec": 5}
        assert plugin_response.status_code == 200
        assert plugin_response.json()["plugin"]["plugin_id"] == "demo"
        assert plugin_response.json()["config"]["source"] == "default"
        assert plugin_response.json()["config"]["config_schema"]["fields"]["theme"]["choices"] == ["neon", "midnight"]
        assert plugin_response.json()["config"]["config"] == {"theme": "neon", "refresh_sec": 5}
        assert api_response.status_code == 200
        assert api_response.json() == {"plugin": "demo"}
        assert web_response.status_code == 200
        assert "demo mounted" in web_response.text
        assert (tmp_path / "plugin-started.txt").read_text(encoding="utf-8") == "started"

    assert (tmp_path / "plugin-stopped.txt").read_text(encoding="utf-8") == "stopped"


def test_create_app_cleans_up_once_when_plugin_startup_fails(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    source_root = tmp_path / "fail-plugin-src"
    _write_failing_lifecycle_plugin(source_root)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with pytest.raises(RuntimeError, match="boom"):
        with TestClient(create_app()):
            pass

    assert (tmp_path / "plugin-started.txt").read_text(encoding="utf-8") == "started"
    assert (tmp_path / "plugin-stopped.txt").read_text(encoding="utf-8") == "1"
