"""Install, inspect, and load embedded AFKBOT plugins."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import importlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlparse
from urllib.request import urlopen

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.plugins.config_store import (
    read_plugin_config,
    reset_plugin_config,
    write_plugin_config,
)
from afkbot.services.plugins.contracts import (
    InstalledPluginRecord,
    PluginConfigMetadata,
    PluginManifest,
    PluginRegistryFile,
    PluginRuntimeSnapshot,
    PluginServiceError,
)
from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry
from afkbot.version import load_cli_version_info

if TYPE_CHECKING:
    from afkbot.services.plugins.contracts import LoadedPluginRuntime, ToolFactory
    from afkbot.services.apps.registry_core import AppRegistry
    from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "PluginService"] = {}
_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "node_modules",
)


class PluginService:
    """Manage installed embedded plugins under the runtime root."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_installed(self) -> tuple[InstalledPluginRecord, ...]:
        """Return installed plugins in deterministic order."""

        payload = self._read_registry()
        return tuple(sorted(payload.plugins, key=lambda item: item.plugin_id))

    def inspect(self, *, plugin_id: str) -> InstalledPluginRecord:
        """Return one installed plugin record."""

        normalized = _normalize_plugin_id(plugin_id)
        for item in self._read_registry().plugins:
            if item.plugin_id == normalized:
                return item
        raise PluginServiceError(
            error_code="plugin_not_found",
            reason=f"Plugin is not installed: {normalized}",
        )

    def get_config(self, *, plugin_id: str) -> PluginConfigMetadata:
        """Return resolved plugin config with source metadata."""

        record = self.inspect(plugin_id=plugin_id)
        config_path = self._config_path(record.plugin_id)
        config, source = self._read_validated_config(record=record)
        return PluginConfigMetadata(
            plugin_id=record.plugin_id,
            source=cast(Literal["default", "persisted"], source),
            config_path=str(config_path),
            data_dir=str(self._ensure_data_dir(record.plugin_id)),
            config_schema=record.manifest.config_schema,
            config=config,
        )

    def set_config(self, *, plugin_id: str, config: Mapping[str, object]) -> PluginConfigMetadata:
        """Persist plugin config as one JSON object."""

        record = self.inspect(plugin_id=plugin_id)
        payload = dict(record.manifest.default_config)
        payload.update(dict(config))
        payload = self._validate_config_payload(record=record, payload=payload)
        write_plugin_config(path=self._config_path(record.plugin_id), payload=payload)
        return self.get_config(plugin_id=record.plugin_id)

    def reset_config(self, *, plugin_id: str) -> PluginConfigMetadata:
        """Delete persisted plugin config and return resolved defaults."""

        record = self.inspect(plugin_id=plugin_id)
        reset_plugin_config(path=self._config_path(record.plugin_id))
        return self.get_config(plugin_id=record.plugin_id)

    def install(
        self,
        *,
        source: str,
        enable: bool = True,
        overwrite: bool = False,
    ) -> InstalledPluginRecord:
        """Install one plugin source from a local directory path."""

        descriptor = self._resolve_source(source)
        source_path, cleanup_root = self._stage_source(descriptor)
        manifest = self._load_manifest(source_path)
        self._validate_manifest_compatibility(manifest)
        install_root = self._install_root(manifest.plugin_id, manifest.version)
        if install_root.exists():
            if not overwrite:
                raise PluginServiceError(
                    error_code="plugin_already_installed",
                    reason=(
                        f"Plugin {manifest.plugin_id}@{manifest.version} is already installed. "
                        "Use --overwrite to replace it."
                    ),
                )
            shutil.rmtree(install_root)
        install_root.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="afkbot-plugin-stage-")).resolve(strict=False)
        try:
            staging_path = staging_root / "plugin"
            shutil.copytree(source_path, staging_path, ignore=_COPY_IGNORE)
            staged_manifest = self._load_manifest(staging_path)
            self._load_entrypoint_callback(install_root=staging_path, manifest=staged_manifest)
            shutil.move(str(staging_path), str(install_root))
            self._ensure_data_dir(manifest.plugin_id)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
            if cleanup_root is not None:
                shutil.rmtree(cleanup_root, ignore_errors=True)

        record = InstalledPluginRecord(
            plugin_id=manifest.plugin_id,
            name=manifest.name,
            version=manifest.version,
            enabled=enable,
            source_kind=descriptor.kind,
            source_ref=descriptor.source_ref,
            install_path=self._to_relative(install_root),
            installed_at=datetime.now(UTC),
            manifest=manifest,
        )
        registry = self._read_registry()
        registry.plugins = [item for item in registry.plugins if item.plugin_id != record.plugin_id]
        registry.plugins.append(record)
        self._write_registry(registry)
        return record

    def update(self, *, plugin_id: str, enable: bool | None = None) -> InstalledPluginRecord:
        """Reinstall one plugin from its persisted source reference."""

        current = self.inspect(plugin_id=plugin_id)
        next_enabled = current.enabled if enable is None else enable
        updated = self.install(
            source=current.source_ref,
            enable=next_enabled,
            overwrite=True,
        )
        previous_path = self._resolve_install_path(current)
        current_path = self._resolve_install_path(updated)
        if previous_path != current_path and previous_path.exists():
            shutil.rmtree(previous_path, ignore_errors=True)
        return updated

    def enable(self, *, plugin_id: str) -> InstalledPluginRecord:
        """Enable one installed plugin."""

        return self._set_enabled(plugin_id=plugin_id, enabled=True)

    def disable(self, *, plugin_id: str) -> InstalledPluginRecord:
        """Disable one installed plugin."""

        return self._set_enabled(plugin_id=plugin_id, enabled=False)

    def remove(self, *, plugin_id: str, purge_files: bool = False) -> InstalledPluginRecord:
        """Remove one plugin from the install registry and optionally delete files."""

        target = self.inspect(plugin_id=plugin_id)
        registry = self._read_registry()
        registry.plugins = [item for item in registry.plugins if item.plugin_id != target.plugin_id]
        self._write_registry(registry)
        if purge_files:
            install_path = self._resolve_install_path(target)
            if install_path.exists():
                shutil.rmtree(install_path)
        return target

    def load_runtime_snapshot(self) -> PluginRuntimeSnapshot:
        """Load every enabled plugin and collect merged runtime surfaces."""

        loaded = []
        seen_tool_factories: dict[str, str] = {}
        seen_api_prefixes: dict[str, str] = {}
        seen_mounts: dict[str, str] = {}
        for record in self.list_installed():
            if not record.enabled:
                continue
            runtime = self._load_runtime_for_record(record)
            api_prefix = runtime.record.manifest.mounts.api_prefix
            if runtime.record.manifest.capabilities.api_router and api_prefix:
                owner = seen_api_prefixes.get(api_prefix)
                if owner is not None:
                    raise PluginServiceError(
                        error_code="plugin_api_prefix_collision",
                        reason=(
                            f"Plugin API prefix collision: {api_prefix} is provided by "
                            f"{owner} and {record.plugin_id}"
                        ),
                    )
                seen_api_prefixes[api_prefix] = record.plugin_id
            for factory_id in runtime.tool_factories:
                owner = seen_tool_factories.get(factory_id)
                if owner is not None:
                    raise PluginServiceError(
                        error_code="plugin_tool_collision",
                        reason=(
                            f"Plugin tool factory id collision: {factory_id} is provided by "
                            f"{owner} and {record.plugin_id}"
                        ),
                    )
                seen_tool_factories[factory_id] = record.plugin_id
            for mount in runtime.static_mounts:
                owner = seen_mounts.get(mount.mount_path)
                if owner is not None:
                    raise PluginServiceError(
                        error_code="plugin_mount_collision",
                        reason=(
                            f"Plugin static mount collision: {mount.mount_path} is provided by "
                            f"{owner} and {record.plugin_id}"
                        ),
                    )
                seen_mounts[mount.mount_path] = record.plugin_id
            loaded.append(runtime)
        return PluginRuntimeSnapshot(plugins=tuple(loaded))

    def register_apps(self, *, registry: AppRegistry) -> None:
        """Apply active plugin app registrars to one merged app registry."""

        for registrar in self.load_runtime_snapshot().app_registrars:
            registrar(registry)

    def tool_factories(self) -> Mapping[str, ToolFactory]:
        """Return merged active plugin tool factories."""

        return self.load_runtime_snapshot().tool_factories

    def skill_dirs(self) -> tuple[Path, ...]:
        """Return active plugin-provided skill roots."""

        return self.load_runtime_snapshot().skill_dirs

    def _set_enabled(self, *, plugin_id: str, enabled: bool) -> InstalledPluginRecord:
        normalized = _normalize_plugin_id(plugin_id)
        registry = self._read_registry()
        updated: InstalledPluginRecord | None = None
        next_items: list[InstalledPluginRecord] = []
        for item in registry.plugins:
            if item.plugin_id != normalized:
                next_items.append(item)
                continue
            updated = item.model_copy(update={"enabled": enabled})
            next_items.append(updated)
        if updated is None:
            raise PluginServiceError(
                error_code="plugin_not_found",
                reason=f"Plugin is not installed: {normalized}",
            )
        registry.plugins = next_items
        self._write_registry(registry)
        return updated

    def _load_runtime_for_record(self, record: InstalledPluginRecord) -> LoadedPluginRuntime:
        install_root = self._resolve_install_path(record)
        if not install_root.exists():
            raise PluginServiceError(
                error_code="plugin_install_missing",
                reason=f"Installed plugin path does not exist: {install_root}",
            )
        manifest = self._load_manifest(install_root)
        registry = PluginRuntimeRegistry(
            plugin_root=install_root,
            record=record,
            manifest=manifest,
            config_path=self._config_path(record.plugin_id),
            data_dir=self._data_dir(record.plugin_id),
        )
        self._register_manifest_surfaces(registry, install_root=install_root, manifest=manifest)
        callback = self._load_entrypoint_callback(install_root=install_root, manifest=manifest)
        callback(registry)
        return registry.snapshot()

    def _register_manifest_surfaces(
        self,
        registry: PluginRuntimeRegistry,
        *,
        install_root: Path,
        manifest: PluginManifest,
    ) -> None:
        if manifest.capabilities.static_web and manifest.paths.web_root and manifest.mounts.web_prefix:
            web_root = registry.resolve_path(manifest.paths.web_root)
            if web_root.exists() and web_root.is_dir():
                registry.register_static_dir(
                    path=manifest.paths.web_root,
                    mount_path=manifest.mounts.web_prefix,
                    name=f"plugin-{manifest.plugin_id}-web",
                )
        if manifest.capabilities.skills and manifest.paths.skills_root:
            skill_root = registry.resolve_path(manifest.paths.skills_root)
            if skill_root.exists() and skill_root.is_dir():
                registry.register_skill_dir(manifest.paths.skills_root)

    def _load_entrypoint_callback(
        self,
        *,
        install_root: Path,
        manifest: PluginManifest,
    ) -> Callable[[PluginRuntimeRegistry], None]:
        python_root = install_root / (manifest.paths.python_root or "python")
        if not python_root.exists() or not python_root.is_dir():
            raise PluginServiceError(
                error_code="plugin_python_root_missing",
                reason=f"Plugin python root is missing: {python_root}",
            )
        module_name, function_name = manifest.entrypoint.split(":", 1)
        _ensure_python_root_on_path(python_root)
        _reset_module_tree(module_name)
        importlib.invalidate_caches()
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise PluginServiceError(
                error_code="plugin_entrypoint_import_failed",
                reason=(
                    f"Failed to import plugin entrypoint module {module_name}: "
                    f"{exc.__class__.__name__}: {exc}"
                ),
            ) from exc
        callback = getattr(module, function_name, None)
        if not callable(callback):
            raise PluginServiceError(
                error_code="plugin_entrypoint_missing",
                reason=(
                    f"Plugin entrypoint function is missing or not callable: "
                    f"{manifest.entrypoint}"
                ),
            )
        return cast(Callable[[PluginRuntimeRegistry], None], callback)

    def _validate_manifest_compatibility(self, manifest: PluginManifest) -> None:
        current_version = load_cli_version_info(root_dir=self._settings.app_dir).version
        if not _version_matches_spec(current_version, manifest.afkbot_version):
            raise PluginServiceError(
                error_code="plugin_incompatible",
                reason=(
                    f"Plugin {manifest.plugin_id}@{manifest.version} requires AFKBOT "
                    f"{manifest.afkbot_version}, current version is {current_version}"
                ),
            )

    def _resolve_source(self, source: str) -> _PluginSourceDescriptor:
        normalized = source.strip()
        if not normalized:
            raise PluginServiceError(
                error_code="plugin_source_invalid",
                reason="Plugin source path is required",
            )
        local_path = _try_resolve_existing_local_path(normalized)
        if local_path is not None:
            return _PluginSourceDescriptor(
                kind="local_path",
                source_ref=str(local_path),
                local_path=local_path,
            )
        github = _parse_github_source(normalized)
        if github is not None:
            return github
        raise PluginServiceError(
            error_code="plugin_source_not_found",
            reason=f"Plugin source is not supported or does not exist: {normalized}",
        )

    def _stage_source(self, descriptor: _PluginSourceDescriptor) -> tuple[Path, Path | None]:
        if descriptor.kind == "local_path":
            local_path = descriptor.local_path
            if local_path is None:
                raise PluginServiceError(
                    error_code="plugin_source_not_found",
                    reason="Resolved local plugin source path is missing",
                )
            return local_path, None
        if descriptor.kind == "github_archive":
            if descriptor.archive_url is None:
                raise PluginServiceError(
                    error_code="plugin_source_invalid",
                    reason="GitHub plugin source did not resolve to an archive URL",
                )
            return _stage_remote_archive(descriptor.archive_url)
        raise PluginServiceError(
            error_code="plugin_source_invalid",
            reason=f"Unsupported plugin source kind: {descriptor.kind}",
        )

    def _load_manifest(self, root: Path) -> PluginManifest:
        manifest_path = root / ".afkbot-plugin" / "plugin.json"
        if not manifest_path.exists():
            raise PluginServiceError(
                error_code="plugin_manifest_missing",
                reason=f"Plugin manifest is missing: {manifest_path}",
            )
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PluginServiceError(
                error_code="plugin_manifest_invalid",
                reason=f"Plugin manifest is invalid JSON: {manifest_path}",
            ) from exc
        try:
            return PluginManifest.model_validate(payload)
        except Exception as exc:
            raise PluginServiceError(
                error_code="plugin_manifest_invalid",
                reason=f"Plugin manifest validation failed: {exc}",
            ) from exc

    def _read_registry(self) -> PluginRegistryFile:
        path = self._settings.plugins_registry_path
        if not path.exists():
            return PluginRegistryFile()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PluginServiceError(
                error_code="plugin_registry_invalid",
                reason=f"Plugin registry is invalid JSON: {path}",
            ) from exc
        try:
            return PluginRegistryFile.model_validate(payload)
        except Exception as exc:
            raise PluginServiceError(
                error_code="plugin_registry_invalid",
                reason=f"Plugin registry validation failed: {exc}",
            ) from exc

    def _write_registry(self, payload: PluginRegistryFile) -> None:
        self._settings.plugins_dir.mkdir(parents=True, exist_ok=True)
        atomic_json_write(
            self._settings.plugins_registry_path,
            payload.model_dump(mode="json"),
            mode=0o600,
        )

    def _install_root(self, plugin_id: str, version: str) -> Path:
        return self._settings.plugins_packages_dir / plugin_id / version

    def _resolve_install_path(self, record: InstalledPluginRecord) -> Path:
        path = Path(record.install_path)
        if not path.is_absolute():
            path = self._settings.root_dir / path
        return path.resolve(strict=False)

    def _config_path(self, plugin_id: str) -> Path:
        return self._settings.plugins_config_dir / f"{plugin_id}.json"

    def _data_dir(self, plugin_id: str) -> Path:
        return self._settings.plugins_data_dir / plugin_id

    def _ensure_data_dir(self, plugin_id: str) -> Path:
        path = self._data_dir(plugin_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _read_validated_config(self, *, record: InstalledPluginRecord) -> tuple[dict[str, object], str]:
        config, source = read_plugin_config(
            path=self._config_path(record.plugin_id),
            default_config=record.manifest.default_config,
        )
        return self._validate_config_payload(record=record, payload=config), source

    def _validate_config_payload(
        self,
        *,
        record: InstalledPluginRecord,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        try:
            return record.manifest.validate_config(dict(payload))
        except ValueError as exc:
            raise PluginServiceError(
                error_code="plugin_config_invalid",
                reason=str(exc),
            ) from exc

    def _to_relative(self, path: Path) -> str:
        resolved_root = self._settings.root_dir.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        try:
            return str(resolved_path.relative_to(resolved_root))
        except ValueError:
            return str(resolved_path)


def _ensure_python_root_on_path(path: Path) -> None:
    normalized = str(path.resolve(strict=False))
    if normalized in sys.path:
        return
    sys.path.insert(0, normalized)


def _reset_module_tree(module_name: str) -> None:
    root_package = module_name.split(".", 1)[0]
    for key in tuple(sys.modules.keys()):
        if key == root_package or key.startswith(f"{root_package}."):
            sys.modules.pop(key, None)


def _normalize_plugin_id(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise PluginServiceError(
            error_code="plugin_invalid_id",
            reason="Plugin id is required",
        )
    return normalized


class _PluginSourceDescriptor:
    def __init__(
        self,
        *,
        kind: Literal["local_path", "github_archive"],
        source_ref: str,
        local_path: Path | None = None,
        archive_url: str | None = None,
    ) -> None:
        self.kind = kind
        self.source_ref = source_ref
        self.local_path = local_path
        self.archive_url = archive_url


def _try_resolve_existing_local_path(source: str) -> Path | None:
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    if not path.exists() or not path.is_dir():
        return None
    return path


def _parse_github_source(source: str) -> _PluginSourceDescriptor | None:
    normalized = source.strip()
    if normalized.startswith("github:"):
        body = normalized.removeprefix("github:").strip()
        owner_repo, _, ref = body.partition("@")
        owner, _, repo = owner_repo.partition("/")
        if not owner or not repo:
            return None
        branch = ref.strip() or "main"
        archive_url = f"https://github.com/{owner}/{repo}/archive/{branch}.tar.gz"
        return _PluginSourceDescriptor(
            kind="github_archive",
            source_ref=f"github:{owner}/{repo}@{branch}",
            archive_url=archive_url,
        )

    url_part, _, explicit_ref = normalized.partition("@")
    parsed = urlparse(url_part)
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"github.com", "www.github.com"}:
        return None
    parts = [item for item in parsed.path.strip("/").split("/") if item]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    branch = explicit_ref.strip() or "main"
    if len(parts) >= 4 and parts[2] == "tree" and parts[3].strip():
        branch = "/".join(parts[3:])
    archive_url = f"https://github.com/{owner}/{repo}/archive/{branch}.tar.gz"
    return _PluginSourceDescriptor(
        kind="github_archive",
        source_ref=f"github:{owner}/{repo}@{branch}",
        archive_url=archive_url,
    )


def _stage_remote_archive(archive_url: str) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="afkbot-plugin-source-")).resolve(strict=False)
    archive_path = temp_dir / "plugin.tar.gz"
    try:
        with urlopen(archive_url, timeout=30) as response, archive_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        raise PluginServiceError(
            error_code="plugin_source_fetch_failed",
            reason=f"Failed to download plugin archive: {archive_url} ({exc})",
        ) from exc
    extract_dir = temp_dir / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract_tar(archive=archive, destination=extract_dir)
    except Exception as exc:
        raise PluginServiceError(
            error_code="plugin_source_extract_failed",
            reason=f"Failed to extract plugin archive: {archive_url} ({exc})",
        ) from exc
    entries = [item for item in extract_dir.iterdir() if item.is_dir()]
    if len(entries) != 1:
        raise PluginServiceError(
            error_code="plugin_source_extract_failed",
            reason=f"Plugin archive did not contain exactly one root directory: {archive_url}",
        )
    return entries[0], temp_dir


def _safe_extract_tar(*, archive: tarfile.TarFile, destination: Path) -> None:
    try:
        archive.extractall(destination, filter="data")
    except tarfile.FilterError as exc:
        member_name = getattr(getattr(exc, "tarinfo", None), "name", "<unknown>")
        raise PluginServiceError(
            error_code="plugin_source_extract_failed",
            reason=f"Plugin archive contains unsafe path: {member_name}",
        ) from exc


def _split_version(value: str) -> tuple[int | str, ...]:
    parts: list[int | str] = []
    for item in value.strip().split("."):
        token = item.strip()
        if not token:
            continue
        parts.append(int(token) if token.isdigit() else token)
    return tuple(parts)


def _compare_versions(left: str, right: str) -> int:
    left_parts = _split_version(left)
    right_parts = _split_version(right)
    width = max(len(left_parts), len(right_parts))
    for index in range(width):
        a = left_parts[index] if index < len(left_parts) else 0
        b = right_parts[index] if index < len(right_parts) else 0
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return -1 if a < b else 1
        return -1 if str(a) < str(b) else 1
    return 0


def _version_matches_spec(current_version: str, spec: str) -> bool:
    normalized_spec = spec.strip()
    if not normalized_spec or normalized_spec == "*":
        return True
    for clause in [item.strip() for item in normalized_spec.split(",") if item.strip()]:
        operator = next(
            (item for item in (">=", "<=", "==", "!=", ">", "<") if clause.startswith(item)),
            None,
        )
        if operator is None:
            target = clause
            if _compare_versions(current_version, target) != 0:
                return False
            continue
        target = clause.removeprefix(operator).strip()
        cmp = _compare_versions(current_version, target)
        if operator == "==" and cmp != 0:
            return False
        if operator == "!=" and cmp == 0:
            return False
        if operator == ">" and cmp <= 0:
            return False
        if operator == ">=" and cmp < 0:
            return False
        if operator == "<" and cmp >= 0:
            return False
        if operator == "<=" and cmp > 0:
            return False
    return True


def get_plugin_service(settings: Settings) -> PluginService:
    """Return cached plugin service for one runtime root."""

    root = str(settings.root_dir.resolve(strict=False))
    service = _SERVICES_BY_ROOT.get(root)
    if service is None:
        service = PluginService(settings)
        _SERVICES_BY_ROOT[root] = service
    return service
