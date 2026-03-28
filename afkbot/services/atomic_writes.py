"""Shared atomic write helpers for small service-owned config files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_text_write(path: Path, content: str, *, mode: int) -> None:
    """Atomically replace one text file and clean up the temp file on failure."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.replace(path)
        path.chmod(mode)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_json_write(path: Path, payload: dict[str, Any], *, mode: int) -> None:
    """Atomically replace one JSON file using the canonical AFKBOT formatting."""

    atomic_text_write(
        path,
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        mode=mode,
    )
