"""Tests for diffs.render tool plugin."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def _registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry.from_plugins(("diffs_render",), settings=settings)


async def test_diffs_render_supports_inline_text_inputs(tmp_path: Path) -> None:
    """diffs.render should return unified and HTML outputs for inline text."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before": "hello\nworld\n",
            "after": "hello\nthere\nworld\n",
            "before_label": "before.txt",
            "after_label": "after.txt",
            "format": "both",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is True
    assert result.payload["changed"] is True
    assert result.payload["before_label"] == "before.txt"
    assert result.payload["after_label"] == "after.txt"
    assert str(result.payload["markdown_preview"]).startswith("**Changes:**")
    assert "@@" in str(result.payload["unified_diff"])
    assert "<html" in str(result.payload["html"]).lower()


async def test_diffs_render_supports_profile_workspace_file_inputs(tmp_path: Path) -> None:
    """diffs.render should resolve relative file paths from the active profile workspace."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "before.txt").write_text("one\ntwo\n", encoding="utf-8")
    (profile_root / "after.txt").write_text("one\nthree\n", encoding="utf-8")

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before_path": "before.txt",
            "after_path": "after.txt",
            "format": "unified",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is True
    assert result.payload["before_label"] == "before.txt"
    assert result.payload["after_label"] == "after.txt"
    assert "```diff" in str(result.payload["markdown_preview"])
    assert "-two" in str(result.payload["unified_diff"])
    assert "+three" in str(result.payload["unified_diff"])


async def test_diffs_render_artifact_mode_persists_outputs(tmp_path: Path) -> None:
    """Artifact mode should persist files and omit inline HTML/diff bodies."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before": "one\ntwo\n",
            "after": "one\nthree\n",
            "format": "both",
            "output_mode": "artifact",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is True
    assert "unified_diff" not in result.payload
    assert "html" not in result.payload
    artifact = result.payload["artifact"]
    assert isinstance(artifact, dict)
    assert Path(str(artifact["files"]["manifest"])).exists()
    assert Path(str(artifact["files"]["unified_diff"])).exists()
    assert Path(str(artifact["files"]["html"])).exists()


async def test_diffs_render_both_mode_keeps_inline_payload_and_artifact(tmp_path: Path) -> None:
    """Both mode should return inline bodies and persisted artifact metadata."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before": "a\n",
            "after": "b\n",
            "format": "unified",
            "output_mode": "both",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is True
    assert "@@" in str(result.payload["unified_diff"])
    artifact = result.payload["artifact"]
    assert isinstance(artifact, dict)
    assert Path(str(artifact["files"]["manifest"])).exists()


async def test_diffs_render_html_only_reports_replaced_line_counts(tmp_path: Path) -> None:
    """HTML-only diff output should still report added/removed counts for replaced lines."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before": "one\ntwo\n",
            "after": "one\nthree\n",
            "format": "html",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is True
    assert result.payload["changed"] is True
    assert result.payload["added_lines"] == 1
    assert result.payload["removed_lines"] == 1
    assert result.payload["html"] is not None


async def test_diffs_render_rejects_missing_source_pairs(tmp_path: Path) -> None:
    """diffs.render should validate that each side has exactly one source."""

    settings = Settings(root_dir=tmp_path)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    with pytest.raises(ValidationError, match="Exactly one of after or after_path is required"):
        tool.parse_params(
            {
                "profile_key": "default",
                "before": "a",
                "after": "b",
                "after_path": "after.txt",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )


async def test_diffs_render_respects_hard_workspace_override_scope(tmp_path: Path) -> None:
    """diffs.render should reject file paths outside explicit hard workspace override."""

    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, tool_workspace_root=shared_root)
    registry = _registry(settings)
    tool = registry.get("diffs.render")
    assert tool is not None

    params = tool.parse_params(
        {
            "profile_key": "default",
            "before": "a",
            "after_path": str(outside),
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await tool.execute(ToolContext(profile_id="default", session_id="s", run_id=1), params)

    assert result.ok is False
    assert result.error_code == "diffs_render_invalid"
    assert "outside scope" in str(result.reason).lower()
