"""Persisted artifact helpers for rendered diffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
from uuid import uuid4

from afkbot.services.diffs.renderer import DiffBundle
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class DiffArtifactBundle:
    """Persisted diff artifact metadata returned to tools and operator surfaces."""

    artifact_id: str
    root_path: Path
    manifest_path: Path
    unified_diff_path: Path | None
    html_path: Path | None
    expires_at: datetime

    def to_payload(self) -> dict[str, object]:
        """Render one deterministic JSON-serializable payload."""

        files: dict[str, str] = {"manifest": str(self.manifest_path)}
        if self.unified_diff_path is not None:
            files["unified_diff"] = str(self.unified_diff_path)
        if self.html_path is not None:
            files["html"] = str(self.html_path)
        payload: dict[str, object] = {
            "id": self.artifact_id,
            "root_path": str(self.root_path),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "files": files,
        }
        if self.html_path is not None:
            payload["viewer_path"] = str(self.html_path)
        return payload


def persist_diff_artifact(
    *,
    settings: Settings,
    bundle: DiffBundle,
    output_format: str,
) -> DiffArtifactBundle:
    """Persist one rendered diff bundle to the artifact store and return metadata."""

    now = datetime.now(tz=UTC)
    cleanup_expired_diff_artifacts(settings=settings, now=now)

    artifact_id = uuid4().hex
    root_path = settings.diffs_artifacts_dir / artifact_id
    root_path.mkdir(parents=True, exist_ok=True)

    unified_diff_path: Path | None = None
    if bundle.unified_diff is not None:
        unified_diff_path = root_path / "diff.patch"
        unified_diff_path.write_text(bundle.unified_diff, encoding="utf-8")

    html_path: Path | None = None
    if bundle.html is not None:
        html_path = root_path / "diff.html"
        html_path.write_text(bundle.html, encoding="utf-8")

    expires_at = now + timedelta(seconds=max(60, int(settings.diffs_artifact_ttl_sec)))
    manifest_path = root_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": artifact_id,
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "output_format": output_format,
                "before_label": bundle.before_label,
                "after_label": bundle.after_label,
                "changed": bundle.changed,
                "added_lines": bundle.added_lines,
                "removed_lines": bundle.removed_lines,
                "files": {
                    "unified_diff": None if unified_diff_path is None else unified_diff_path.name,
                    "html": None if html_path is None else html_path.name,
                },
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return DiffArtifactBundle(
        artifact_id=artifact_id,
        root_path=root_path,
        manifest_path=manifest_path,
        unified_diff_path=unified_diff_path,
        html_path=html_path,
        expires_at=expires_at,
    )


def cleanup_expired_diff_artifacts(*, settings: Settings, now: datetime | None = None) -> int:
    """Delete expired diff artifact directories and return removed count."""

    root_dir = settings.diffs_artifacts_dir
    if not root_dir.exists():
        return 0
    current = datetime.now(tz=UTC) if now is None else now
    removed = 0
    for candidate in root_dir.iterdir():
        if not candidate.is_dir():
            continue
        if _artifact_is_expired(candidate=candidate, now=current, ttl_sec=settings.diffs_artifact_ttl_sec):
            shutil.rmtree(candidate, ignore_errors=True)
            removed += 1
    return removed


def _artifact_is_expired(*, candidate: Path, now: datetime, ttl_sec: int) -> bool:
    manifest = candidate / "manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        raw_expires = str(payload.get("expires_at") or "").strip()
        if raw_expires:
            try:
                expires_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
            except ValueError:
                return True
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            return expires_at <= now
        return True
    try:
        modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
    except OSError:
        return True
    return modified_at + timedelta(seconds=max(60, int(ttl_sec))) <= now
