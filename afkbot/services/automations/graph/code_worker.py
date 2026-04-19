"""Isolated worker used by code graph nodes.

This file is executed as a standalone script by `CodePythonNodeAdapter`.
It intentionally depends only on the Python standard library so it can run
under `python -I`.
"""

from __future__ import annotations

import asyncio
import ast
import _io
import builtins
import importlib.util as _importlib_util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
from typing import Any
from collections.abc import Callable

try:  # pragma: no cover - Unix-only capability
    import resource
except ImportError:  # pragma: no cover - non-Unix fallback
    resource = None


class SandboxViolation(RuntimeError):
    """Raised when user code attempts one blocked sandbox operation."""


def _load_module(path: Path) -> object:
    spec = _importlib_util.spec_from_file_location("automation_graph_node_code", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load code node module: {path}")
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_result(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        payload = {str(key): inner for key, inner in value.items()}
        ports = payload.get("ports")
        if isinstance(ports, dict):
            return {
                "ok": True,
                "ports": {str(key): inner for key, inner in ports.items()},
                "selected_ports": [
                    str(item) for item in payload.get("selected_ports", tuple(ports.keys()))
                ],
                "metadata": {
                    str(key): inner for key, inner in dict(payload.get("metadata") or {}).items()
                },
                "effects": list(payload.get("effects") or []),
                "unsafe_side_effects": bool(payload.get("unsafe_side_effects", False)),
            }
        return {
            "ok": True,
            "ports": {"default": payload},
            "selected_ports": ["default"],
            "metadata": {},
            "effects": [],
            "unsafe_side_effects": False,
        }
    return {
        "ok": True,
        "ports": {"default": value},
        "selected_ports": ["default"],
        "metadata": {},
        "effects": [],
        "unsafe_side_effects": False,
    }


def _apply_resource_limits(policy: dict[str, Any]) -> None:
    if resource is None:
        return
    memory_limit = int(policy.get("memory_limit_bytes") or 128 * 1024 * 1024)
    limits: list[tuple[str, int]] = [
        ("RLIMIT_CPU", int(policy.get("cpu_time_sec") or 2)),
        ("RLIMIT_FSIZE", int(policy.get("max_file_size_bytes") or 1024 * 1024)),
        ("RLIMIT_NOFILE", int(policy.get("max_open_files") or 32)),
        ("RLIMIT_CORE", 0),
    ]
    if hasattr(resource, "RLIMIT_AS"):
        limits.append(("RLIMIT_AS", memory_limit))
    elif hasattr(resource, "RLIMIT_DATA"):
        limits.append(("RLIMIT_DATA", memory_limit))
    for name, limit in limits:
        resource_name = getattr(resource, name, None)
        if resource_name is None:
            continue
        try:
            resource.setrlimit(resource_name, (limit, limit))
        except (OSError, ValueError):
            continue


def _apply_runtime_guards(*, source_path: Path) -> None:
    sandbox_root = source_path.parent.resolve()
    stdlib_root = Path(sysconfig.get_paths()["stdlib"]).resolve()
    allowed_read_roots = (sandbox_root, stdlib_root)
    os.chdir(sandbox_root)
    os.umask(0o077)
    _install_import_guard()
    _install_subprocess_guard()
    _install_network_guard()
    for name in (
        "__main__",
        "_socket",
        "_ssl",
        "_posixsubprocess",
        "asyncio.subprocess",
        "ctypes",
        "importlib",
        "importlib.machinery",
        "importlib.util",
        "multiprocessing",
        "posix",
        "socket",
        "ssl",
        "subprocess",
    ):
        sys.modules.pop(name, None)
    _install_filesystem_guard(
        sandbox_root=sandbox_root,
        allowed_read_roots=allowed_read_roots,
    )
    # Hide the worker module surface so code nodes cannot reach privileged helpers via sys.modules.
    sys.modules.pop("__main__", None)


def _install_import_guard() -> None:
    original_import = builtins.__import__
    blocked_prefixes = (
        "__main__",
        "_socket",
        "_ssl",
        "_posixsubprocess",
        "asyncio.subprocess",
        "ctypes",
        "importlib",
        "multiprocessing",
        "posix",
        "socket",
        "ssl",
        "subprocess",
    )

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        _ = globals, locals, fromlist, level
        if any(name == item or name.startswith(f"{item}.") for item in blocked_prefixes):
            raise SandboxViolation(f"Sandbox import denied: {name}")
        return original_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import


def _validate_source_ast(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    blocked_imports = (
        "__main__",
        "_socket",
        "_ssl",
        "_posixsubprocess",
        "asyncio.subprocess",
        "ctypes",
        "importlib",
        "multiprocessing",
        "posix",
        "socket",
        "ssl",
        "subprocess",
    )
    blocked_calls = {
        "__import__",
        "compile",
        "eval",
        "exec",
    }
    blocked_attributes = {
        ("os", "execl"),
        ("os", "execle"),
        ("os", "execlp"),
        ("os", "execlpe"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "execvp"),
        ("os", "execvpe"),
        ("os", "fork"),
        ("os", "forkpty"),
        ("os", "posix_spawn"),
        ("os", "posix_spawnp"),
        ("os", "spawnl"),
        ("os", "spawnle"),
        ("os", "spawnlp"),
        ("os", "spawnlpe"),
        ("os", "spawnv"),
        ("os", "spawnve"),
        ("os", "spawnvp"),
        ("os", "spawnvpe"),
        ("os", "system"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("subprocess", "run"),
    }

    class _Guard(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if any(
                    alias.name == item or alias.name.startswith(f"{item}.")
                    for item in blocked_imports
                ):
                    raise SandboxViolation(f"Sandbox import denied: {alias.name}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module_name = node.module or ""
            if any(
                module_name == item or module_name.startswith(f"{item}.")
                for item in blocked_imports
            ):
                raise SandboxViolation(f"Sandbox import denied: {module_name}")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            target = _call_target(node.func)
            if target in blocked_calls:
                raise SandboxViolation(f"Sandbox call denied: {target}")
            if target is not None and tuple(target.split(".", 1)) in blocked_attributes:
                raise SandboxViolation(f"Sandbox call denied: {target}")
            self.generic_visit(node)

    _Guard().visit(tree)


def _install_subprocess_guard() -> None:
    def blocked(*_args: object, **_kwargs: object) -> object:
        raise SandboxViolation("Sandbox subprocess execution denied")

    subprocess.Popen = blocked  # type: ignore[assignment]
    subprocess.run = blocked  # type: ignore[assignment]
    subprocess.call = blocked  # type: ignore[assignment]
    subprocess.check_call = blocked  # type: ignore[assignment]
    subprocess.check_output = blocked  # type: ignore[assignment]
    subprocess.getoutput = blocked  # type: ignore[assignment]
    subprocess.getstatusoutput = blocked  # type: ignore[assignment]
    asyncio.create_subprocess_exec = blocked  # type: ignore[assignment]
    asyncio.create_subprocess_shell = blocked  # type: ignore[assignment]
    os.system = blocked  # type: ignore[assignment]
    for name in (
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    ):
        if hasattr(os, name):
            setattr(os, name, blocked)


def _install_network_guard() -> None:
    def blocked(*_args: object, **_kwargs: object) -> object:
        raise SandboxViolation("Sandbox network access denied")

    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.fromfd = blocked  # type: ignore[assignment]
    if hasattr(socket, "socketpair"):
        socket.socketpair = blocked  # type: ignore[assignment]


def _install_filesystem_guard(
    *,
    sandbox_root: Path,
    allowed_read_roots: tuple[Path, ...],
) -> None:
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_access = os.access
    original_listdir = os.listdir
    original_scandir = os.scandir
    original_stat = os.stat
    original_lstat = os.lstat
    original_readlink = os.readlink
    original_remove = os.remove
    original_unlink = os.unlink
    original_rename = os.rename
    original_replace = os.replace
    original_link = getattr(os, "link", None)
    original_symlink = getattr(os, "symlink", None)
    original_utime = os.utime
    original_chmod = os.chmod
    original_chown = getattr(os, "chown", None)
    original_lchmod = getattr(os, "lchmod", None)
    original_lchown = getattr(os, "lchown", None)
    original_chflags = getattr(os, "chflags", None)
    original_lchflags = getattr(os, "lchflags", None)
    original_truncate = os.truncate
    original_mkdir = os.mkdir
    original_makedirs = os.makedirs
    original_rmdir = os.rmdir

    def resolve_real_path(raw_path: str) -> str:
        saved_lstat = os.lstat
        saved_readlink = os.readlink
        try:
            os.lstat = original_lstat  # type: ignore[assignment]
            os.readlink = original_readlink  # type: ignore[assignment]
            return os.path.realpath(raw_path)
        finally:
            os.lstat = saved_lstat  # type: ignore[assignment]
            os.readlink = saved_readlink  # type: ignore[assignment]

    def guard_path(path: object, *, write: bool) -> Path | None:
        resolved = _resolve_user_path(path, realpath=resolve_real_path)
        if resolved is None:
            return None
        if write:
            if not _is_relative_to(resolved, sandbox_root):
                raise SandboxViolation(f"Sandbox write denied: {resolved}")
            return resolved
        if _is_relative_to(resolved, sandbox_root):
            return resolved
        if any(_is_relative_to(resolved, root) for root in allowed_read_roots):
            return resolved
        raise SandboxViolation(f"Sandbox read denied: {resolved}")

    def guarded_open(
        file: object,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener=None,
    ):
        guard_path(file, write=_mode_requires_write(mode))
        return original_open(
            file,
            mode,
            buffering,
            encoding,
            errors,
            newline,
            closefd,
            opener,
        )

    def guarded_io_open(
        file: object,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener=None,
    ):
        guard_path(file, write=_mode_requires_write(mode))
        return original_io_open(
            file,
            mode,
            buffering,
            encoding,
            errors,
            newline,
            closefd,
            opener,
        )

    def guarded_os_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        _reject_fd_relative_access(dir_fd)
        guard_path(path, write=_flags_require_write(flags))
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_remove(path: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        guard_path(path, write=True)
        original_remove(path, *args, **kwargs)

    def guarded_listdir(path: object = ".") -> list[str]:
        guard_path(path, write=False)
        return original_listdir(path)

    def guarded_scandir(path: object = "."):
        guard_path(path, write=False)
        return original_scandir(path)

    def guarded_stat(path: object, *args: object, **kwargs: object):
        _ = args, kwargs
        guard_path(path, write=False)
        return original_stat(path, *args, **kwargs)

    def guarded_lstat(path: object, *args: object, **kwargs: object):
        _ = args, kwargs
        guard_path(path, write=False)
        return original_lstat(path, *args, **kwargs)

    def guarded_readlink(path: object, *args: object, **kwargs: object) -> str:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        guard_path(path, write=False)
        return original_readlink(path, *args, **kwargs)

    def guarded_access(
        path: object,
        mode: int,
        *args: object,
        **kwargs: object,
    ) -> bool:
        _ = mode, args, kwargs
        guard_path(path, write=False)
        return original_access(path, mode, *args, **kwargs)

    def guarded_unlink(path: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        guard_path(path, write=True)
        original_unlink(path, *args, **kwargs)

    def guarded_rename(src: object, dst: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("src_dir_fd"), kwargs.get("dst_dir_fd"))
        guard_path(src, write=True)
        guard_path(dst, write=True)
        original_rename(src, dst, *args, **kwargs)

    def guarded_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("src_dir_fd"), kwargs.get("dst_dir_fd"))
        guard_path(src, write=True)
        guard_path(dst, write=True)
        original_replace(src, dst, *args, **kwargs)

    def guarded_utime(path: object, *args: object, **kwargs: object) -> None:
        guard_path(path, write=True)
        original_utime(path, *args, **kwargs)

    def guarded_chmod(path: object, *args: object, **kwargs: object) -> None:
        guard_path(path, write=True)
        original_chmod(path, *args, **kwargs)

    def guarded_truncate(path: object, *args: object, **kwargs: object) -> None:
        guard_path(path, write=True)
        original_truncate(path, *args, **kwargs)

    def guarded_optional_write_api(
        original_fn,
        path: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        guard_path(path, write=True)
        original_fn(path, *args, **kwargs)

    def guarded_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("src_dir_fd"), kwargs.get("dst_dir_fd"))
        resolved_src = _resolve_user_path(src, realpath=resolve_real_path)
        resolved_dst = _resolve_user_path(dst, realpath=resolve_real_path)
        if resolved_src is None or resolved_dst is None:
            raise SandboxViolation("Sandbox link denied")
        if not _is_relative_to(resolved_src, sandbox_root) or not _is_relative_to(
            resolved_dst, sandbox_root
        ):
            raise SandboxViolation("Sandbox link denied")
        if original_link is None:  # pragma: no cover - platform-specific
            raise SandboxViolation("Sandbox link denied")
        original_link(src, dst, *args, **kwargs)

    def guarded_symlink(src: object, dst: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        resolved_src = _resolve_user_path(src, realpath=resolve_real_path)
        resolved_dst = _resolve_user_path(dst, realpath=resolve_real_path)
        if resolved_src is None or resolved_dst is None:
            raise SandboxViolation("Sandbox symlink denied")
        if not _is_relative_to(resolved_src, sandbox_root) or not _is_relative_to(
            resolved_dst, sandbox_root
        ):
            raise SandboxViolation("Sandbox symlink denied")
        if original_symlink is None:  # pragma: no cover - platform-specific
            raise SandboxViolation("Sandbox symlink denied")
        original_symlink(src, dst, *args, **kwargs)

    def guarded_mkdir(path: object, mode: int = 0o777, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        guard_path(path, write=True)
        original_mkdir(path, mode, *args, **kwargs)

    def guarded_makedirs(
        name: object,
        mode: int = 0o777,
        exist_ok: bool = False,
    ) -> None:
        guard_path(name, write=True)
        original_makedirs(name, mode=mode, exist_ok=exist_ok)

    def guarded_rmdir(path: object, *args: object, **kwargs: object) -> None:
        _reject_fd_relative_access(kwargs.get("dir_fd"))
        guard_path(path, write=True)
        original_rmdir(path, *args, **kwargs)

    def blocked_raw_file(*_args: object, **_kwargs: object) -> object:
        raise SandboxViolation("Sandbox raw file access denied")

    builtins.open = guarded_open
    io.open = guarded_io_open  # type: ignore[assignment]
    if hasattr(io, "FileIO"):
        io.FileIO = blocked_raw_file  # type: ignore[assignment]
    if hasattr(io, "open_code"):
        io.open_code = blocked_raw_file  # type: ignore[assignment]
    if hasattr(_io, "FileIO"):
        _io.FileIO = blocked_raw_file  # type: ignore[assignment]
    if hasattr(_io, "open"):
        _io.open = guarded_io_open  # type: ignore[assignment]
    if hasattr(_io, "open_code"):
        _io.open_code = guarded_io_open  # type: ignore[assignment]
    os.open = guarded_os_open  # type: ignore[assignment]
    os.access = guarded_access  # type: ignore[assignment]
    os.listdir = guarded_listdir  # type: ignore[assignment]
    os.scandir = guarded_scandir  # type: ignore[assignment]
    os.stat = guarded_stat  # type: ignore[assignment]
    os.lstat = guarded_lstat  # type: ignore[assignment]
    os.readlink = guarded_readlink  # type: ignore[assignment]
    os.remove = guarded_remove  # type: ignore[assignment]
    os.unlink = guarded_unlink  # type: ignore[assignment]
    os.rename = guarded_rename  # type: ignore[assignment]
    os.replace = guarded_replace  # type: ignore[assignment]
    os.utime = guarded_utime  # type: ignore[assignment]
    os.chmod = guarded_chmod  # type: ignore[assignment]
    os.truncate = guarded_truncate  # type: ignore[assignment]
    if original_link is not None:
        os.link = guarded_link  # type: ignore[assignment]
    if original_symlink is not None:
        os.symlink = guarded_symlink  # type: ignore[assignment]
    if original_chown is not None:
        os.chown = lambda path, *args, **kwargs: guarded_optional_write_api(  # type: ignore[assignment]
            original_chown, path, *args, **kwargs
        )
    if original_lchmod is not None:
        os.lchmod = lambda path, *args, **kwargs: guarded_optional_write_api(  # type: ignore[assignment]
            original_lchmod, path, *args, **kwargs
        )
    if original_lchown is not None:
        os.lchown = lambda path, *args, **kwargs: guarded_optional_write_api(  # type: ignore[assignment]
            original_lchown, path, *args, **kwargs
        )
    if original_chflags is not None:
        os.chflags = lambda path, *args, **kwargs: guarded_optional_write_api(  # type: ignore[assignment]
            original_chflags, path, *args, **kwargs
        )
    if original_lchflags is not None:
        os.lchflags = lambda path, *args, **kwargs: guarded_optional_write_api(  # type: ignore[assignment]
            original_lchflags, path, *args, **kwargs
        )
    os.mkdir = guarded_mkdir  # type: ignore[assignment]
    os.makedirs = guarded_makedirs  # type: ignore[assignment]
    os.rmdir = guarded_rmdir  # type: ignore[assignment]
    os.chdir = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[assignment]
        SandboxViolation("Sandbox chdir denied")
    )


def _resolve_user_path(
    path: object,
    *,
    realpath: Callable[[str], str] | None = None,
) -> Path | None:
    if isinstance(path, int):
        return None
    try:
        raw_path = os.fspath(path)
    except TypeError:
        return None
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    absolute_path = os.path.abspath(str(candidate))
    if realpath is None:
        return Path(absolute_path)
    return Path(realpath(absolute_path))


def _reject_fd_relative_access(*values: object) -> None:
    if any(value is not None for value in values):
        raise SandboxViolation("Sandbox fd-relative filesystem access denied")


def _mode_requires_write(mode: str) -> bool:
    return any(marker in mode for marker in ("+", "a", "w", "x"))


def _flags_require_write(flags: int) -> bool:
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    return bool(flags & write_flags)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


async def _run(path: Path, request: dict[str, Any]) -> dict[str, object]:
    _apply_resource_limits(dict(request.get("sandbox") or {}))
    _apply_runtime_guards(source_path=path)
    _validate_source_ast(path)
    module = _load_module(path)
    run_callable = getattr(module, "run", None)
    if run_callable is None:
        raise RuntimeError("Code node module must define `run(context, inputs, config)`")
    result = run_callable(
        request.get("context") or {},
        request.get("inputs") or {},
        request.get("config") or {},
    )
    if asyncio.iscoroutine(result):
        result = await result
    return _normalize_result(result)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: code_worker.py <source_file>", file=sys.stderr)
        return 2
    path = Path(argv[1]).resolve()
    request = json.loads(sys.stdin.read() or "{}")
    try:
        response = asyncio.run(_run(path, request))
    except SandboxViolation as exc:  # pragma: no cover - exercised through adapter tests
        response = {
            "ok": False,
            "error_code": "graph_node_sandbox_violation",
            "reason": str(exc).strip() or type(exc).__name__,
            "metadata": {"exception_type": type(exc).__name__},
        }
    except PermissionError as exc:  # pragma: no cover - exercised through adapter tests
        response = {
            "ok": False,
            "error_code": "graph_node_sandbox_violation",
            "reason": str(exc).strip() or type(exc).__name__,
            "metadata": {"exception_type": type(exc).__name__},
        }
    except Exception as exc:  # pragma: no cover - exercised through adapter tests
        response = {
            "ok": False,
            "error_code": "graph_node_failed",
            "reason": str(exc).strip() or type(exc).__name__,
            "metadata": {"exception_type": type(exc).__name__},
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    return 0


def _call_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_target(node.value)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


if __name__ == "__main__":  # pragma: no cover - executed in subprocess
    raise SystemExit(main(sys.argv))
