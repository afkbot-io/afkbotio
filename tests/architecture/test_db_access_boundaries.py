"""Architecture guard tests for runtime database access boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPO_ROOT / "afkbot"


def test_runtime_uses_one_async_sessionmaker_entrypoint() -> None:
    """Session factories should stay centralized so SQLite write gating is unavoidable."""

    allowed = {_SOURCE_ROOT / "db" / "session.py"}
    violations: list[str] = []

    for file_path in _iter_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) != "async_sessionmaker":
                continue
            if file_path not in allowed:
                violations.append(_format_violation(file_path, node.lineno, "async_sessionmaker"))

    assert not violations, "DB session factory boundary violations:\n" + "\n".join(violations)


def test_runtime_does_not_open_sqlite_connections_directly() -> None:
    """Runtime code should not bypass SQLAlchemy engine/session configuration."""

    violations: list[str] = []

    for file_path in _iter_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _dotted_call_name(node.func) in {"sqlite3.connect", "aiosqlite.connect"}:
                violations.append(_format_violation(file_path, node.lineno, "direct sqlite connect"))

    assert not violations, "Raw SQLite connection boundary violations:\n" + "\n".join(violations)


def test_runtime_engine_begin_sections_stay_behind_bootstrap_gate() -> None:
    """Raw engine transactions are schema/maintenance-only and must stay explicitly gated."""

    allowed = {_SOURCE_ROOT / "db" / "bootstrap_runtime.py"}
    violations: list[str] = []

    for file_path in _iter_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "begin":
                if file_path not in allowed:
                    violations.append(_format_violation(file_path, node.lineno, ".begin()"))

    assert not violations, "Raw engine transaction boundary violations:\n" + "\n".join(violations)


def _iter_source_files() -> list[Path]:
    return sorted(
        file_path
        for file_path in _SOURCE_ROOT.rglob("*.py")
        if "__pycache__" not in file_path.parts
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dotted_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _format_violation(file_path: Path, line_number: int, reason: str) -> str:
    return f"{file_path.relative_to(_REPO_ROOT)}:{line_number} uses {reason}"
