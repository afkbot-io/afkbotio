"""Architecture guard tests for CLI command module boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "afkbot.db",
    "afkbot.repositories",
)


def test_cli_commands_do_not_import_db_or_repositories_directly() -> None:
    """Ensure CLI command entry points depend on services, not data layers."""

    commands_dir = Path("afkbot/cli/commands")
    violations: list[str] = []

    for file_path in sorted(commands_dir.glob("*.py")):
        if file_path.name == "__init__.py":
            continue
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_forbidden(module):
                    violations.append(f"{file_path}:{node.lineno} imports {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    if _is_forbidden(name):
                        violations.append(f"{file_path}:{node.lineno} imports {name}")

    assert not violations, "CLI boundary violations:\n" + "\n".join(violations)


def _is_forbidden(module: str) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in _FORBIDDEN_IMPORT_PREFIXES)
