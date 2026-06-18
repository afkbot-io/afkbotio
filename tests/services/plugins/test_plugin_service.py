"""Tests for embedded plugin install and runtime loading."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tarfile
import threading
import time

import pytest

from afkbot.services.apps.registry import get_app_registry
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
    run_channel_endpoint_service_sync,
)
from afkbot.services.plugins import PluginServiceError, get_plugin_service, scaffold_plugin
from afkbot.services.plugins import service as plugin_service_module
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.services.skills.loader_service import SkillLoader
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def _write_demo_plugin(root: Path) -> None:
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    (root / "python/afkbot_plugin_demo").mkdir(parents=True, exist_ok=True)
    (root / "web/dist").mkdir(parents=True, exist_ok=True)
    (root / "skills/demo-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "demo",
                "name": "Demo Plugin",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": "afkbot_plugin_demo.plugin:register",
                "default_config": {"poll_interval_sec": 5, "theme": "neon"},
                "config_schema": {
                    "fields": {
                        "poll_interval_sec": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 300,
                        },
                        "theme": {
                            "type": "string",
                            "choices": ["neon", "midnight"],
                        },
                    },
                },
                "permissions": {
                    "database": "read",
                    "taskflow": "read",
                    "outbound_http": False,
                    "data_dir_write": True,
                },
                "capabilities": {
                    "api_router": True,
                    "static_web": True,
                    "tools": True,
                    "skills": True,
                    "apps": True,
                },
                "mounts": {
                    "api_prefix": "/v1/plugins/demo",
                    "web_prefix": "/plugins/demo",
                },
                "paths": {
                    "python_root": "python",
                    "web_root": "web/dist",
                    "skills_root": "skills",
                },
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

from afkbot.services.apps.registry_core import AppDefinition
from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult


class DemoTool(ToolBase):
    name = "demo.echo"
    description = "Echo payload from demo plugin."

    async def execute(self, ctx: ToolContext, params):
        _ = ctx
        return ToolResult(ok=True, payload={"echo": params.model_dump(mode="python")})


async def _app_handler(settings, ctx, action, params):
    _ = settings, ctx, action
    return ToolResult(ok=True, payload={"demo": params})


def _register_apps(app_registry):
    app_registry.register(
        AppDefinition(
            name="demoapp",
            handler=_app_handler,
            allowed_skills=frozenset({"demo-plugin"}),
            allowed_actions=frozenset({"ping"}),
            source="plugin",
        ),
        replace_existing=True,
    )


def register(registry: PluginRuntimeRegistry) -> None:
    router = APIRouter(prefix=registry.manifest.mounts.api_prefix or "/v1/plugins/demo")

    @router.get("/ping")
    async def ping():
        return {"plugin": "demo"}

    registry.register_router(router)
    registry.register_tool_factory("demo_tool", lambda settings: DemoTool())
    registry.register_skill_dir("skills")
    registry.register_app_registrar(_register_apps)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "web/dist/index.html").write_text(
        "<html><body>demo plugin</body></html>\n", encoding="utf-8"
    )
    (root / "skills/demo-plugin/SKILL.md").write_text(
        "---\nname: demo-plugin\ndescription: Demo plugin skill.\n---\n# Demo\n",
        encoding="utf-8",
    )


def _update_demo_manifest(root: Path, *, version: str, afkbot_version: str = "*") -> None:
    manifest_path = root / ".afkbot-plugin/plugin.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["version"] = version
    payload["afkbot_version"] = afkbot_version
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _write_channel_plugin(
    root: Path,
    *,
    transport: str = "avito",
    adapter_kind: str = "avito_polling",
    channels: bool = True,
    outbound_http: bool = True,
    data_dir_write: bool = True,
    build_runtime: bool = False,
) -> None:
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    (root / "python/afkbot_plugin_avito").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "avito",
                "name": "Avito Channel",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": "afkbot_plugin_avito.plugin:register",
                "default_config": {},
                "config_schema": {"fields": {}},
                "permissions": {
                    "database": "none",
                    "taskflow": "none",
                    "outbound_http": outbound_http,
                    "data_dir_write": data_dir_write,
                },
                "capabilities": {
                    "channels": channels,
                },
                "mounts": {
                    "api_prefix": None,
                    "web_prefix": None,
                },
                "paths": {
                    "python_root": "python",
                    "web_root": None,
                    "skills_root": None,
                },
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "python/afkbot_plugin_avito/__init__.py").write_text("", encoding="utf-8")
    (root / "python/afkbot_plugin_avito/plugin.py").write_text(
        f"""
from __future__ import annotations

from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory
from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry


async def _send_message(settings, target, message, credential_profile_key):
    _ = settings, credential_profile_key
    return {{"provider_message_id": "m-1", "target": target.to_payload(), "text": message.text}}


def _target_key(target):
    return target.peer_id or target.address


class _Runtime:
    async def start(self):
        pass

    async def stop(self):
        pass


def _build_runtime(settings, endpoint, state_dir):
    _ = settings, endpoint, state_dir
    return _Runtime()


def register(registry: PluginRuntimeRegistry) -> None:
    registry.register_channel_adapter(
        ChannelAdapterFactory(
            transport="{transport}",
            adapter_kind="{adapter_kind}",
            build_runtime={("_build_runtime" if build_runtime else "None")},
            send_message=_send_message,
            outbound_target_key=_target_key,
        )
    )
""".strip()
        + "\n",
        encoding="utf-8",
    )


async def _create_channel_endpoint_for_plugin(settings: Settings) -> None:
    profiles = ProfileService(settings)
    endpoints = get_channel_endpoint_service(settings)
    try:
        await profiles.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("*",),
        )
        await endpoints.create(
            ChannelEndpointConfig(
                endpoint_id="avito-main",
                transport="avito",
                adapter_kind="avito_polling",
                profile_id="default",
                credential_profile_key="avito-main",
                account_id="seller-1",
            )
        )
    finally:
        await profiles.shutdown()
        await endpoints.shutdown()


def test_plugin_service_installs_and_loads_runtime(tmp_path: Path) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)

    record = get_plugin_service(settings).install(source=str(source_root))
    snapshot = get_plugin_service(settings).load_runtime_snapshot()

    assert record.plugin_id == "demo"
    assert snapshot.plugins[0].record.plugin_id == "demo"
    assert snapshot.routers
    assert snapshot.static_mounts[0].mount_path == "/plugins/demo"
    assert "demo_tool" in snapshot.tool_factories
    assert snapshot.skill_dirs
    assert snapshot.app_registrars


def test_plugin_service_loads_channel_adapters(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root)
    settings = Settings(root_dir=tmp_path)

    get_plugin_service(settings).install(source=str(source_root))
    snapshot = get_plugin_service(settings).load_runtime_snapshot()

    assert ("avito", "avito_polling") in snapshot.channel_adapters
    assert snapshot.channel_adapters[("avito", "avito_polling")].send_message is not None


def test_plugin_service_requires_channel_capability_for_channel_adapter(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root, channels=False)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    with pytest.raises(PluginServiceError) as error_info:
        service.load_runtime_snapshot()

    assert error_info.value.error_code == "plugin_channel_capability_required"


def test_plugin_service_requires_outbound_http_for_channel_io(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root, outbound_http=False)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    with pytest.raises(PluginServiceError) as error_info:
        service.load_runtime_snapshot()

    assert error_info.value.error_code == "plugin_channel_permission_required"


def test_plugin_service_requires_data_dir_write_for_channel_runtime(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root, data_dir_write=False, build_runtime=True)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    with pytest.raises(PluginServiceError) as error_info:
        service.load_runtime_snapshot()

    assert error_info.value.error_code == "plugin_channel_permission_required"
    assert "data_dir_write" in error_info.value.reason


def test_plugin_service_rejects_reserved_builtin_channel_transport(tmp_path: Path) -> None:
    source_root = tmp_path / "telegram-plugin-src"
    _write_channel_plugin(
        source_root,
        transport="telegram",
        adapter_kind="custom_polling",
    )
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    try:
        service.load_runtime_snapshot()
    except PluginServiceError as exc:
        assert exc.error_code == "plugin_channel_transport_reserved"
        assert "telegram" in exc.reason
    else:
        raise AssertionError("Expected built-in channel transport rejection")


def test_plugin_service_blocks_disable_when_channel_endpoints_depend_on_plugin(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root)
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_channels.db'}",
    )
    service = get_plugin_service(settings)
    service.install(source=str(source_root))
    asyncio.run(_create_channel_endpoint_for_plugin(settings))

    with pytest.raises(PluginServiceError) as error_info:
        service.disable(plugin_id="avito")

    assert error_info.value.error_code == "plugin_channel_endpoints_exist"
    assert "avito-main" in error_info.value.reason
    disabled = service.disable(plugin_id="avito", force=True)
    assert disabled.enabled is False


def test_plugin_service_blocks_channel_plugin_disable_when_adapter_inspection_fails(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root)
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_broken_channel.db'}",
    )
    service = get_plugin_service(settings)
    installed = service.install(source=str(source_root))
    asyncio.run(_create_channel_endpoint_for_plugin(settings))
    installed_root = settings.root_dir / installed.install_path
    (installed_root / "python/afkbot_plugin_avito/plugin.py").write_text(
        "raise RuntimeError('broken plugin import')\n",
        encoding="utf-8",
    )

    with pytest.raises(PluginServiceError) as error_info:
        service.disable(plugin_id="avito")

    assert error_info.value.error_code == "plugin_channel_adapter_inspection_failed"
    disabled = service.disable(plugin_id="avito", force=True)
    assert disabled.enabled is False


def test_plugin_service_remove_can_delete_plugin_channel_endpoints(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root)
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_remove_channels.db'}",
    )
    service = get_plugin_service(settings)
    service.install(source=str(source_root))
    asyncio.run(_create_channel_endpoint_for_plugin(settings))

    removed = service.remove(plugin_id="avito", delete_channel_endpoints=True)

    assert removed.plugin_id == "avito"
    with pytest.raises(PluginServiceError) as error_info:
        service.inspect(plugin_id="avito")
    assert error_info.value.error_code == "plugin_not_found"
    with pytest.raises(ChannelEndpointServiceError) as endpoint_error:
        run_channel_endpoint_service_sync(
            settings,
            lambda endpoint_service: endpoint_service.get(endpoint_id="avito-main"),
        )
    assert endpoint_error.value.error_code == "channel_endpoint_not_found"


def test_plugin_service_blocks_update_that_drops_channel_adapter_key(tmp_path: Path) -> None:
    source_root = tmp_path / "avito-plugin-src"
    _write_channel_plugin(source_root)
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_update_channels.db'}",
    )
    service = get_plugin_service(settings)
    service.install(source=str(source_root))
    asyncio.run(_create_channel_endpoint_for_plugin(settings))
    plugin_file = source_root / "python/afkbot_plugin_avito/plugin.py"
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace("avito_polling", "avito_v2_polling"),
        encoding="utf-8",
    )

    with pytest.raises(PluginServiceError) as error_info:
        service.update(plugin_id="avito")

    assert error_info.value.error_code == "plugin_channel_update_incompatible"
    assert "avito/avito_polling" in error_info.value.reason
    updated = service.update(plugin_id="avito", force=True)
    assert updated.plugin_id == "avito"


def test_plugin_service_reads_and_persists_plugin_config(tmp_path: Path) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    default_config = service.get_config(plugin_id="demo")
    assert default_config.source == "default"
    assert default_config.config == {"poll_interval_sec": 5, "theme": "neon"}
    assert Path(default_config.data_dir).exists()

    persisted = service.set_config(
        plugin_id="demo",
        config={"theme": "midnight"},
    )
    assert persisted.source == "persisted"
    assert persisted.config == {"poll_interval_sec": 5, "theme": "midnight"}
    assert Path(persisted.config_path).exists()
    assert persisted.config_schema.fields["poll_interval_sec"].type == "integer"

    runtime = service.load_runtime_snapshot()
    plugin = runtime.plugins[0]
    assert plugin.record.plugin_id == "demo"
    assert plugin.startup_hooks == ()
    assert plugin.shutdown_hooks == ()
    assert plugin.routers

    reset = service.reset_config(plugin_id="demo")
    assert reset.source == "default"
    assert reset.config == {"poll_interval_sec": 5, "theme": "neon"}


def test_plugin_service_rejects_invalid_plugin_config(tmp_path: Path) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    service.install(source=str(source_root))

    try:
        service.set_config(
            plugin_id="demo",
            config={"poll_interval_sec": 0, "theme": "midnight"},
        )
    except PluginServiceError as exc:
        assert "poll_interval_sec" in str(exc)
    else:
        raise AssertionError("Expected config validation error for poll_interval_sec")

    try:
        service.set_config(
            plugin_id="demo",
            config={"poll_interval_sec": 5, "theme": "midnight", "extra": "boom"},
        )
    except PluginServiceError as exc:
        assert "Unknown plugin config keys" in str(exc)
    else:
        raise AssertionError("Expected config validation error for unknown key")


def test_plugin_runtime_integrates_with_tools_skills_and_apps(tmp_path: Path) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)
    get_plugin_service(settings).install(source=str(source_root))

    tool_registry = ToolRegistry.from_plugins(("demo_tool",), settings=settings)
    skill_loader = SkillLoader(settings)
    app_registry = get_app_registry(settings=settings, profile_id="default")
    visible_skills = asyncio.run(skill_loader.list_skills("default"))

    assert tool_registry.get("demo.echo") is not None
    assert any(item.name == "demo-plugin" for item in visible_skills)
    assert app_registry.get("demoapp") is not None


def test_plugin_service_update_reinstalls_from_saved_source(tmp_path: Path) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)

    installed = service.install(source=str(source_root))
    install_root = settings.root_dir / installed.install_path
    installed_index = install_root / "web/dist/index.html"
    assert "demo plugin" in installed_index.read_text(encoding="utf-8")

    (source_root / "web/dist/index.html").write_text(
        "<html><body>demo plugin updated</body></html>\n", encoding="utf-8"
    )

    updated = service.update(plugin_id="demo", enable=False)

    assert updated.enabled is False
    assert updated.source_ref == str(source_root.resolve(strict=False))
    assert "demo plugin updated" in installed_index.read_text(encoding="utf-8")


def test_plugin_service_concurrent_runtime_loads_do_not_break_package_import(
    tmp_path: Path,
) -> None:
    """Concurrent runtime loads should not reset one plugin package out from under another import."""

    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    manifest_path = source_root / ".afkbot-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plugin_id"] = "race"
    manifest["name"] = "Race Plugin"
    manifest["entrypoint"] = "afkbot_plugin_race.plugin:register"
    manifest["mounts"]["api_prefix"] = "/v1/plugins/race"
    manifest["mounts"]["web_prefix"] = "/plugins/race"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )

    package_root = source_root / "python"
    (package_root / "afkbot_plugin_demo").rename(package_root / "afkbot_plugin_race")
    plugin_path = package_root / "afkbot_plugin_race/plugin.py"
    plugin_path.write_text(
        plugin_path.read_text(encoding="utf-8").replace("demo", "race"),
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path)
    service = get_plugin_service(settings)
    installed = service.install(source=str(source_root))
    installed_root = settings.root_dir / installed.install_path
    imported_package_dir = installed_root / "python" / "afkbot_plugin_race"
    started_marker = imported_package_dir / ".started"
    continue_marker = imported_package_dir / ".continue"
    imported_package_dir.joinpath("__init__.py").write_text(
        """
from __future__ import annotations

from pathlib import Path
import time

package_dir = Path(__file__).resolve().parent
started_marker = package_dir / ".started"
continue_marker = package_dir / ".continue"
started_marker.write_text("started", encoding="utf-8")
deadline = time.monotonic() + 5.0
while not continue_marker.exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("timed out waiting for plugin import gate")
    time.sleep(0.01)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors: list[BaseException] = []
    snapshots = []

    def _load_runtime() -> None:
        try:
            snapshots.append(service.load_runtime_snapshot())
        except BaseException as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    first = threading.Thread(target=_load_runtime, name="plugin-load-1")
    second = threading.Thread(target=_load_runtime, name="plugin-load-2")

    first.start()
    deadline = time.monotonic() + 2.0
    while not started_marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("plugin import did not reach package gate in time")
        time.sleep(0.01)

    second.start()
    time.sleep(0.1)
    continue_marker.write_text("go", encoding="utf-8")
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert errors == []
    assert len(snapshots) == 2


def test_plugin_service_installs_from_github_archive_source(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "demo-plugin-src"
    _write_demo_plugin(source_root)
    settings = Settings(root_dir=tmp_path)

    archive_bytes = _build_plugin_archive(root=source_root, archive_root_name="demo-plugin-main")

    class _ArchiveResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _fake_urlopen(url: str, timeout: int = 30):
        _ = timeout
        assert url == "https://github.com/acme/demo-plugin/archive/main.tar.gz"
        return _ArchiveResponse(archive_bytes)

    source_temp_root = tmp_path / "source-temp"
    stage_temp_root = tmp_path / "stage-temp"

    def _fake_mkdtemp(*, prefix: str) -> str:
        if prefix.startswith("afkbot-plugin-source-"):
            source_temp_root.mkdir(parents=True, exist_ok=True)
            return str(source_temp_root)
        if prefix.startswith("afkbot-plugin-stage-"):
            stage_temp_root.mkdir(parents=True, exist_ok=True)
            return str(stage_temp_root)
        raise AssertionError(f"Unexpected tempdir prefix: {prefix}")

    monkeypatch.setattr(plugin_service_module, "urlopen", _fake_urlopen)
    monkeypatch.setattr(plugin_service_module.tempfile, "mkdtemp", _fake_mkdtemp)

    installed = get_plugin_service(settings).install(source="github:acme/demo-plugin@main")

    assert installed.plugin_id == "demo"
    assert installed.source_kind == "github_archive"
    assert installed.source_ref == "github:acme/demo-plugin@main"
    assert (settings.root_dir / installed.install_path / ".afkbot-plugin/plugin.json").exists()
    assert not source_temp_root.exists()
    assert not stage_temp_root.exists()


def test_plugin_service_update_uses_latest_compatible_github_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current_root = tmp_path / "demo-plugin-v1.0.14"
    latest_root = tmp_path / "demo-plugin-v1.0.16"
    incompatible_root = tmp_path / "demo-plugin-v1.0.17"
    for root, version, body in (
        (current_root, "1.0.14", "current"),
        (latest_root, "1.0.16", "latest compatible"),
        (incompatible_root, "1.0.17", "incompatible"),
    ):
        _write_demo_plugin(root)
        _update_demo_manifest(
            root,
            version=version,
            afkbot_version=">=99.0.0" if version == "1.0.17" else "*",
        )
        (root / "web/dist/index.html").write_text(
            f"<html><body>{body}</body></html>\n",
            encoding="utf-8",
        )
    archives = {
        "https://github.com/acme/demo-plugin/archive/v1.0.14.tar.gz": _build_plugin_archive(
            root=current_root,
            archive_root_name="demo-plugin-v1.0.14",
        ),
        "https://github.com/acme/demo-plugin/archive/v1.0.16.tar.gz": _build_plugin_archive(
            root=latest_root,
            archive_root_name="demo-plugin-v1.0.16",
        ),
        "https://github.com/acme/demo-plugin/archive/v1.0.17.tar.gz": _build_plugin_archive(
            root=incompatible_root,
            archive_root_name="demo-plugin-v1.0.17",
        ),
    }
    releases_payload = json.dumps(
        [
            {"draft": False, "prerelease": False, "tag_name": "v1.0.17"},
            {"draft": False, "prerelease": False, "tag_name": "v1.0.16"},
            {"draft": False, "prerelease": False, "tag_name": "v1.0.14"},
        ],
        ensure_ascii=True,
    ).encode("utf-8")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _fake_urlopen(url: str, timeout: int = 30):
        _ = timeout
        if url == "https://api.github.com/repos/acme/demo-plugin/releases?per_page=50":
            return _Response(releases_payload)
        return _Response(archives[url])

    monkeypatch.setattr(plugin_service_module, "urlopen", _fake_urlopen)
    service = get_plugin_service(Settings(root_dir=tmp_path))
    installed = service.install(source="github:acme/demo-plugin@v1.0.14")

    updated = service.update(plugin_id="demo")

    assert installed.source_ref == "github:acme/demo-plugin@v1.0.14"
    assert updated.version == "1.0.16"
    assert updated.source_ref == "github:acme/demo-plugin@v1.0.16"
    assert "latest compatible" in (
        tmp_path / updated.install_path / "web/dist/index.html"
    ).read_text(encoding="utf-8")


def test_plugin_service_update_moves_github_main_source_to_latest_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    main_root = tmp_path / "demo-plugin-main"
    latest_root = tmp_path / "demo-plugin-v1.0.16"
    for root, version, body in (
        (main_root, "1.0.14", "main snapshot"),
        (latest_root, "1.0.16", "latest release"),
    ):
        _write_demo_plugin(root)
        _update_demo_manifest(root, version=version)
        (root / "web/dist/index.html").write_text(
            f"<html><body>{body}</body></html>\n",
            encoding="utf-8",
        )
    archives = {
        "https://github.com/acme/demo-plugin/archive/main.tar.gz": _build_plugin_archive(
            root=main_root,
            archive_root_name="demo-plugin-main",
        ),
        "https://github.com/acme/demo-plugin/archive/v1.0.16.tar.gz": _build_plugin_archive(
            root=latest_root,
            archive_root_name="demo-plugin-v1.0.16",
        ),
    }
    releases_payload = json.dumps(
        [{"draft": False, "prerelease": False, "tag_name": "v1.0.16"}],
        ensure_ascii=True,
    ).encode("utf-8")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _fake_urlopen(url: str, timeout: int = 30):
        _ = timeout
        if url == "https://api.github.com/repos/acme/demo-plugin/releases?per_page=50":
            return _Response(releases_payload)
        return _Response(archives[url])

    monkeypatch.setattr(plugin_service_module, "urlopen", _fake_urlopen)
    service = get_plugin_service(Settings(root_dir=tmp_path))
    installed = service.install(source="github:acme/demo-plugin@main")

    updated = service.update(plugin_id="demo")

    assert installed.source_ref == "github:acme/demo-plugin@main"
    assert updated.version == "1.0.16"
    assert updated.source_ref == "github:acme/demo-plugin@v1.0.16"
    assert "latest release" in (
        tmp_path / updated.install_path / "web/dist/index.html"
    ).read_text(encoding="utf-8")


def test_plugin_service_install_latest_resolves_compatible_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    latest_root = tmp_path / "demo-plugin-v1.0.16"
    _write_demo_plugin(latest_root)
    _update_demo_manifest(latest_root, version="1.0.16")
    archive_bytes = _build_plugin_archive(
        root=latest_root,
        archive_root_name="demo-plugin-v1.0.16",
    )
    releases_payload = json.dumps(
        [{"draft": False, "prerelease": False, "tag_name": "v1.0.16"}],
        ensure_ascii=True,
    ).encode("utf-8")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _fake_urlopen(url: str, timeout: int = 30):
        _ = timeout
        if url == "https://api.github.com/repos/acme/demo-plugin/releases?per_page=50":
            return _Response(releases_payload)
        assert url == "https://github.com/acme/demo-plugin/archive/v1.0.16.tar.gz"
        return _Response(archive_bytes)

    monkeypatch.setattr(plugin_service_module, "urlopen", _fake_urlopen)

    installed = get_plugin_service(Settings(root_dir=tmp_path)).install(
        source="github:acme/demo-plugin@latest"
    )

    assert installed.version == "1.0.16"
    assert installed.source_ref == "github:acme/demo-plugin@v1.0.16"


def _build_plugin_archive(*, root: Path, archive_root_name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in root.rglob("*"):
            archive.add(path, arcname=f"{archive_root_name}/{path.relative_to(root)}")
    return buffer.getvalue()


def test_plugin_scaffold_creates_installable_embedded_plugin(tmp_path: Path) -> None:
    plugin_root = tmp_path / "afkbot-plugin-demo"
    result = scaffold_plugin(
        destination=plugin_root,
        plugin_id="demo",
        name="Demo Plugin",
        static_web=True,
        api_router=True,
        skills=True,
        lifecycle=True,
        channel=True,
    )

    assert result.manifest_path.exists()
    assert result.entrypoint_path.exists()
    assert (plugin_root / "python/afkbot_plugin_demo/router.py").exists()
    assert (plugin_root / "web/dist/index.html").exists()
    assert (plugin_root / "skills/demo/SKILL.md").exists()
    assert (plugin_root / "python/afkbot_plugin_demo/channel.py").exists()
    assert (plugin_root / "docs/CHANNEL_INTEGRATION_SPEC.md").exists()
    assert '"lifecycle": true' in result.manifest_path.read_text(encoding="utf-8")
    assert '"channels": true' in result.manifest_path.read_text(encoding="utf-8")
    assert "PluginChannelIngressDispatcher" in (
        plugin_root / "docs/CHANNEL_INTEGRATION_SPEC.md"
    ).read_text(encoding="utf-8")

    settings = Settings(root_dir=tmp_path / "runtime")
    installed = get_plugin_service(settings).install(source=str(plugin_root))
    snapshot = get_plugin_service(settings).load_runtime_snapshot()

    assert installed.plugin_id == "demo"
    assert snapshot.plugins[0].record.plugin_id == "demo"
    assert snapshot.routers
    assert snapshot.static_mounts
    assert snapshot.skill_dirs
    assert ("demo", "demo_polling") in snapshot.channel_adapters


def test_plugin_scaffold_uses_python_safe_package_for_hyphenated_plugin_id(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "afkbot-plugin-demo-channel"
    result = scaffold_plugin(
        destination=plugin_root,
        plugin_id="demo-channel",
        name="Demo Channel",
        api_router=True,
        static_web=False,
        channel=True,
    )

    assert result.entrypoint_path == plugin_root / "python/afkbot_plugin_demo_channel/plugin.py"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["plugin_id"] == "demo-channel"
    assert manifest["entrypoint"] == "afkbot_plugin_demo_channel.plugin:register"
    assert (plugin_root / "python/afkbot_plugin_demo_channel/router.py").exists()
    assert (plugin_root / "python/afkbot_plugin_demo_channel/channel.py").exists()

    settings = Settings(root_dir=tmp_path / "runtime")
    installed = get_plugin_service(settings).install(source=str(plugin_root))
    snapshot = get_plugin_service(settings).load_runtime_snapshot()

    assert installed.plugin_id == "demo-channel"
    assert ("demo-channel", "demo-channel_polling") in snapshot.channel_adapters
