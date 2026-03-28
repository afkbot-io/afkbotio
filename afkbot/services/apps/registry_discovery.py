"""Builtin and profile app discovery helpers for the app registry."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import importlib
import importlib.util
import inspect
import logging
import pkgutil
from pathlib import Path
from types import ModuleType

from afkbot.services.apps.registry_core import AppHandler, AppRegistry, build_register_app
from afkbot.services.path_scope import resolve_in_scope_or_none
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings

_logger = logging.getLogger(__name__)
_builtin_discovery_done = False


def ensure_builtin_apps_loaded() -> None:
    """Import builtin app packages exactly once so decorators can register handlers."""

    global _builtin_discovery_done
    if _builtin_discovery_done:
        return

    package = importlib.import_module("afkbot.services.apps")
    package_paths = getattr(package, "__path__", None)
    if package_paths is None:
        _builtin_discovery_done = True
        return

    for module in pkgutil.iter_modules(package_paths):
        if module.ispkg and not module.name.startswith("_"):
            importlib.import_module(f"{package.__name__}.{module.name}")
    _builtin_discovery_done = True


def discover_profile_apps(*, registry: AppRegistry, settings: Settings, profile_id: str) -> None:
    """Load profile-local app modules into a copy of the builtin registry."""

    if not bool(getattr(settings, "enable_profile_app_modules", False)):
        return
    apps_root = _profile_apps_root(settings=settings, profile_id=profile_id)
    if apps_root is None:
        return
    for module_path in _iter_profile_app_modules(apps_root=apps_root):
        _load_profile_module(registry=registry, file_path=module_path)


def _profile_apps_root(*, settings: Settings, profile_id: str) -> Path | None:
    try:
        normalized_profile_id = validate_profile_id(profile_id)
    except ValueError:
        return None
    profiles_root = settings.profiles_dir.resolve()
    candidate = resolve_in_scope_or_none(
        profiles_root / normalized_profile_id / "apps",
        scope_root=profiles_root,
        strict=False,
    )
    if candidate is None or not candidate.exists() or not candidate.is_dir():
        return None
    return candidate


def _iter_profile_app_modules(*, apps_root: Path) -> tuple[Path, ...]:
    resolved_root = apps_root.resolve()
    candidates: dict[str, Path] = {}

    for path in sorted(apps_root.glob("*.py"), key=lambda item: item.name):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        safe_path = resolve_in_scope_or_none(path, scope_root=resolved_root, strict=True)
        if safe_path is None or not safe_path.is_file():
            continue
        candidates[safe_path.as_posix()] = safe_path

    for directory in sorted(apps_root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or directory.name.startswith("_"):
            continue
        app_path = directory / "APP.py"
        safe_path = resolve_in_scope_or_none(app_path, scope_root=resolved_root, strict=True)
        if safe_path is None or not safe_path.is_file():
            continue
        candidates[safe_path.as_posix()] = safe_path

    return tuple(candidates[key] for key in sorted(candidates))


def _call_registration_hook(
    hook: object,
    *,
    registry: AppRegistry,
    register_app_for_module: Callable[[object], Callable[[AppHandler], AppHandler]],
) -> None:
    if not callable(hook):
        return
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        hook(registry)
        return

    kwargs: dict[str, object] = {}
    parameters = signature.parameters
    if "registry" in parameters:
        kwargs["registry"] = registry
    if "register_app" in parameters:
        kwargs["register_app"] = register_app_for_module
    if kwargs:
        hook(**kwargs)
        return

    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if not positional:
        hook()
    elif len(positional) == 1:
        hook(registry)
    else:
        hook(registry, register_app_for_module)


def _load_profile_module(*, registry: AppRegistry, file_path: Path) -> None:
    module_hash = hashlib.sha1(file_path.as_posix().encode("utf-8")).hexdigest()
    module_name = f"afkbot_profile_apps_{module_hash}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return

    register_app_for_module = build_register_app(
        registry=registry,
        source="profile",
        source_path=file_path.as_posix(),
    )
    module = importlib.util.module_from_spec(spec)
    setattr(module, "register_app", register_app_for_module)

    try:
        spec.loader.exec_module(module)
    except Exception:
        _logger.exception("Failed to load profile app module: %s", file_path.as_posix())
        return

    _register_from_module(
        module,
        registry=registry,
        register_app_for_module=register_app_for_module,
    )


def _register_from_module(
    module: ModuleType,
    *,
    registry: AppRegistry,
    register_app_for_module: Callable[[object], Callable[[AppHandler], AppHandler]],
) -> None:
    hook: object | None = None
    for name in ("register_apps", "register"):
        candidate = getattr(module, name, None)
        if callable(candidate):
            hook = candidate
            break
    if hook is None:
        return
    try:
        _call_registration_hook(
            hook,
            registry=registry,
            register_app_for_module=register_app_for_module,
        )
    except Exception:
        _logger.exception(
            "Failed to register profile app definitions from module: %s",
            getattr(module, "__name__", "<unknown>"),
        )
