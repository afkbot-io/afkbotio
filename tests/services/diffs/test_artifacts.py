"""Tests for persisted diff artifact helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from afkbot.services.diffs import cleanup_expired_diff_artifacts, persist_diff_artifact
from afkbot.services.diffs.renderer import DiffBundle
from afkbot.settings import Settings


def _bundle() -> DiffBundle:
    return DiffBundle(
        before_label="before.txt",
        after_label="after.txt",
        added_lines=1,
        removed_lines=1,
        changed=True,
        unified_diff="--- before.txt\n+++ after.txt\n@@\n-old\n+new\n",
        html="<html><body>diff</body></html>",
    )


def test_persist_diff_artifact_writes_files_and_manifest(tmp_path: Path) -> None:
    """Persisted diff artifacts should include files and one manifest."""

    settings = Settings(root_dir=tmp_path)
    artifact = persist_diff_artifact(settings=settings, bundle=_bundle(), output_format="both")

    assert artifact.root_path.exists()
    assert artifact.manifest_path.exists()
    assert artifact.unified_diff_path is not None and artifact.unified_diff_path.exists()
    assert artifact.html_path is not None and artifact.html_path.exists()
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_format"] == "both"
    assert manifest["files"]["unified_diff"] == "diff.patch"
    assert manifest["files"]["html"] == "diff.html"


def test_cleanup_expired_diff_artifacts_removes_stale_dirs(tmp_path: Path) -> None:
    """Cleanup should delete expired artifact directories based on manifest expiry."""

    settings = Settings(root_dir=tmp_path, diffs_artifact_ttl_sec=600)
    root_dir = settings.diffs_artifacts_dir / "stale"
    root_dir.mkdir(parents=True)
    (root_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": "stale",
                "expires_at": (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            }
        ),
        encoding="utf-8",
    )

    removed = cleanup_expired_diff_artifacts(settings=settings)

    assert removed == 1
    assert not root_dir.exists()
