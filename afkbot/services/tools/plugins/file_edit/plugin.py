"""Tool plugin for file.edit."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.services.tools.text_snapshots import snapshot_inline_text
from afkbot.services.tools.workspace import (
    resolve_io_path,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    to_workspace_relative,
)
from afkbot.settings import Settings


class FileEditParams(RoutedToolParameters):
    """Parameters for file.edit tool."""

    path: str = Field(min_length=1, max_length=4096)
    search: str = Field(min_length=1)
    replace: str = Field(default="")
    replace_all: bool = False


class FileEditTool(ToolBase):
    """Perform deterministic textual replacement in one file."""

    name = "file.edit"
    description = "Replace text in one file."
    parameters_model = FileEditParams
    required_skill = "file-ops"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=FileEditParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        try:
            base_dir = resolve_tool_workspace_base_dir(settings=self._settings, profile_id=ctx.profile_id)
            scope_roots = await resolve_tool_workspace_scope_roots(
                settings=self._settings,
                profile_id=ctx.profile_id,
            )
            path = resolve_io_path(
                base_dir=base_dir,
                scope_roots=scope_roots,
                raw_path=payload.path,
                must_exist=True,
            )
            if not path.is_file():
                raise ValueError(f"Path is not a file: {payload.path}")

            content = path.read_text(encoding="utf-8")
            if payload.search not in content:
                return ToolResult.error(
                    error_code="file_edit_pattern_not_found",
                    reason="Search pattern was not found in file",
                )

            count = content.count(payload.search)
            if payload.replace_all:
                updated = content.replace(payload.search, payload.replace)
                replacements = count
            else:
                updated = content.replace(payload.search, payload.replace, 1)
                replacements = 1

            path.write_text(updated, encoding="utf-8")
            preview_limit = min(self._settings.runtime_max_body_bytes, 32_768)
            before_text, before_truncated, before_size_bytes = snapshot_inline_text(
                text=content,
                max_bytes=preview_limit,
            )
            after_text, after_truncated, after_size_bytes = snapshot_inline_text(
                text=updated,
                max_bytes=preview_limit,
            )
            result_payload: dict[str, object] = {
                "path": to_workspace_relative(base_dir=base_dir, path=path),
                "replacements": replacements,
                "before_text": before_text,
                "before_truncated": before_truncated,
                "before_size_bytes": before_size_bytes,
                "after_text": after_text,
                "after_truncated": after_truncated,
                "after_size_bytes": after_size_bytes,
            }
            if not before_truncated and not after_truncated:
                result_payload["diff_suggestion"] = {
                    "before": before_text,
                    "after": after_text,
                    "before_label": f"{path.name} (before)",
                    "after_label": path.name,
                    "format": "unified",
                    "output_mode": "inline",
                }
            return ToolResult(
                ok=True,
                payload=result_payload,
            )
        except (ValueError, UnicodeError) as exc:
            return ToolResult.error(error_code="file_edit_invalid", reason=str(exc))
        except OSError as exc:
            return ToolResult.error(error_code="file_edit_failed", reason=f"{exc.__class__.__name__}: {exc}")


def create_tool(settings: Settings) -> ToolBase:
    """Create file.edit tool instance."""

    return FileEditTool(settings=settings)
