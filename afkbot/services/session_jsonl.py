"""Shared JSONL session-sharded storage primitives."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True, slots=True)
class JsonlSessionPaths:
    """Resolved JSONL data, lock, and sequence paths for one session shard."""

    event_path: Path
    lock_path: Path
    seq_path: Path


def jsonl_paths_for_session(
    *,
    root_dir: Path,
    namespace: str,
    session_id: str,
) -> JsonlSessionPaths:
    """Return stable sharded JSONL paths for one logical session."""

    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    directory = root_dir / namespace / digest[:2]
    base = directory / digest
    return JsonlSessionPaths(
        event_path=base.with_suffix(".jsonl"),
        lock_path=base.with_suffix(".lock"),
        seq_path=base.with_suffix(".seq"),
    )


def next_jsonl_sequence_id(seq_path: Path) -> int:
    """Increment and return a small per-session JSONL sequence id."""

    current = 0
    if seq_path.exists():
        raw = seq_path.read_text(encoding="utf-8").strip()
        if raw:
            current = int(raw)
    next_id = current + 1
    seq_path.write_text(f"{next_id}\n", encoding="utf-8")
    return next_id
