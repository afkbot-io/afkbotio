"""Tests for embedded plugin install and runtime loading."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tarfile
import threading
import time

from afkbot.services.apps.registry import get_app_registry
from afkbot.services.plugins import PluginServiceError, get_plugin_service, scaffold_plugin
from afkbot.services.plugins import service as plugin_service_module
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
                "default_config": {
                    "poll_interval_sec": 5,
                    "theme": "neon"
                },
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
    (root / "web/dist/index.html").write_text("<html><body>demo plugin</body></html>\n", encoding="utf-8")
    (root / "skills/demo-plugin/SKILL.md").write_text(
        "---\nname: demo-plugin\ndescription: Demo plugin skill.\n---\n# Demo\n",
        encoding="utf-8",
    )


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

    (source_root / "web/dist/index.html").write_text("<html><body>demo plugin updated</body></html>\n", encoding="utf-8")

    updated = service.update(plugin_id="demo", enable=False)

    assert updated.enabled is False
    assert updated.source_ref == str(source_root.resolve(strict=False))
    assert "demo plugin updated" in installed_index.read_text(encoding="utf-8")


def test_plugin_service_concurrent_runtime_loads_do_not_break_package_import(tmp_path: Path) -> None:
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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

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
    )

    assert result.manifest_path.exists()
    assert result.entrypoint_path.exists()
    assert (plugin_root / "python/afkbot_plugin_demo/router.py").exists()
    assert (plugin_root / "web/dist/index.html").exists()
    assert (plugin_root / "skills/demo/SKILL.md").exists()
    assert '"lifecycle": true' in result.manifest_path.read_text(encoding="utf-8")

    settings = Settings(root_dir=tmp_path / "runtime")
    installed = get_plugin_service(settings).install(source=str(plugin_root))
    snapshot = get_plugin_service(settings).load_runtime_snapshot()

    assert installed.plugin_id == "demo"
    assert snapshot.plugins[0].record.plugin_id == "demo"
    assert snapshot.routers
    assert snapshot.static_mounts
    assert snapshot.skill_dirs
