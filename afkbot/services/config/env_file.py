"""Helpers for updating AFKBOT env files from CLI commands."""

from __future__ import annotations

from pathlib import Path


def upsert_env_values(path: Path, values: dict[str, str]) -> None:
    """Create or update key-value pairs in one dotenv-like file."""

    normalized = {key.strip(): value for key, value in values.items() if key.strip()}
    if not normalized:
        return

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []

    index_by_key: dict[str, int] = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key:
            index_by_key[key] = idx

    for key, value in normalized.items():
        rendered = f"{key}={value}"
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            lines.append(rendered)
        else:
            lines[existing_idx] = rendered

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
