"""Tool plugin for diffs.render."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from afkbot.services.diffs import persist_diff_artifact, render_diff_bundle
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.services.tools.workspace import (
    resolve_io_path,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    to_workspace_relative,
)
from afkbot.settings import Settings


class DiffsRenderParams(RoutedToolParameters):
    """Parameters for diffs.render."""

    before: str | None = Field(default=None, max_length=200_000)
    before_path: str | None = Field(default=None, min_length=1, max_length=4096)
    before_label: str | None = Field(default=None, max_length=256)
    after: str | None = Field(default=None, max_length=200_000)
    after_path: str | None = Field(default=None, min_length=1, max_length=4096)
    after_label: str | None = Field(default=None, max_length=256)
    format: str = Field(default="both", pattern="^(unified|html|both)$")
    output_mode: str = Field(default="inline", pattern="^(inline|artifact|both)$")
    context_lines: int = Field(default=3, ge=0, le=20)
    max_chars_per_input: int = Field(default=50_000, ge=1, le=200_000)

    @model_validator(mode="after")
    def _validate_sources(self) -> DiffsRenderParams:
        if (self.before is None) == (self.before_path is None):
            raise ValueError("Exactly one of before or before_path is required")
        if (self.after is None) == (self.after_path is None):
            raise ValueError("Exactly one of after or after_path is required")
        return self


class DiffsRenderTool(ToolBase):
    """Render unified and HTML diffs from text or profile-scoped files."""

    name = "diffs.render"
    description = "Render unified or HTML diff from text or file inputs."
    parameters_model = DiffsRenderParams
    required_skill = "diffs"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=DiffsRenderParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        try:
            base_dir = resolve_tool_workspace_base_dir(settings=self._settings, profile_id=ctx.profile_id)
            scope_roots = await resolve_tool_workspace_scope_roots(
                settings=self._settings,
                profile_id=ctx.profile_id,
            )
            before_text, before_label, before_truncated = self._resolve_source(
                inline_text=payload.before,
                raw_path=payload.before_path,
                explicit_label=payload.before_label,
                default_label="before",
                base_dir=base_dir,
                scope_roots=scope_roots,
                max_chars=payload.max_chars_per_input,
            )
            after_text, after_label, after_truncated = self._resolve_source(
                inline_text=payload.after,
                raw_path=payload.after_path,
                explicit_label=payload.after_label,
                default_label="after",
                base_dir=base_dir,
                scope_roots=scope_roots,
                max_chars=payload.max_chars_per_input,
            )
            bundle = render_diff_bundle(
                before_text=before_text,
                after_text=after_text,
                before_label=before_label,
                after_label=after_label,
                output_format=payload.format,
                context_lines=payload.context_lines,
            )
            result_payload: dict[str, object] = {
                "before_label": bundle.before_label,
                "after_label": bundle.after_label,
                "changed": bundle.changed,
                "added_lines": bundle.added_lines,
                "removed_lines": bundle.removed_lines,
                "before_truncated": before_truncated,
                "after_truncated": after_truncated,
                "format": payload.format,
                "output_mode": payload.output_mode,
            }
            if bundle.markdown_preview is not None:
                result_payload["markdown_preview"] = bundle.markdown_preview
            if payload.output_mode in {"inline", "both"} and bundle.unified_diff is not None:
                result_payload["unified_diff"] = bundle.unified_diff
            if payload.output_mode in {"inline", "both"} and bundle.html is not None:
                result_payload["html"] = bundle.html
            if payload.output_mode in {"artifact", "both"}:
                artifact = persist_diff_artifact(
                    settings=self._settings,
                    bundle=bundle,
                    output_format=payload.format,
                )
                result_payload["artifact"] = artifact.to_payload()
            return ToolResult(ok=True, payload=result_payload)
        except ValueError as exc:
            return ToolResult.error(error_code="diffs_render_invalid", reason=str(exc))
        except OSError as exc:
            return ToolResult.error(error_code="diffs_render_failed", reason=f"{exc.__class__.__name__}: {exc}")

    @staticmethod
    def _resolve_source(
        *,
        inline_text: str | None,
        raw_path: str | None,
        explicit_label: str | None,
        default_label: str,
        base_dir: Path,
        scope_roots: tuple[Path, ...],
        max_chars: int,
    ) -> tuple[str, str, bool]:
        if inline_text is not None:
            truncated = len(inline_text) > max_chars
            text = inline_text[:max_chars]
            label = (explicit_label or default_label).strip() or default_label
            return text, label, truncated
        if raw_path is None:
            raise ValueError(f"Missing {default_label} source")
        resolved = resolve_io_path(
            base_dir=base_dir,
            scope_roots=scope_roots,
            raw_path=raw_path,
            must_exist=True,
        )
        if not resolved.is_file():
            raise ValueError(f"Path is not a file: {raw_path}")
        text, truncated = _read_text_prefix(path=resolved, max_chars=max_chars)
        label = (explicit_label or to_workspace_relative(base_dir=base_dir, path=resolved)).strip() or default_label
        return text, label, truncated


def _read_text_prefix(*, path: Path, max_chars: int) -> tuple[str, bool]:
    read_limit = max(1, max_chars) + 1
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        data = handle.read(read_limit)
    truncated = len(data) > max_chars
    return data[:max_chars], truncated


def create_tool(settings: Settings) -> ToolBase:
    """Create diffs.render tool instance."""

    return DiffsRenderTool(settings=settings)
