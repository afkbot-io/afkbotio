"""Tool plugin for file.write."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.services.tools.text_snapshots import snapshot_path_text
from afkbot.services.tools.workspace import (
    resolve_io_path,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    to_workspace_relative,
)
from afkbot.settings import Settings


class FileWriteParams(RoutedToolParameters):
    """Parameters for file.write tool."""

    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(default="")
    mode: str = Field(default="overwrite", pattern="^(overwrite|append)$")
    create_dirs: bool = True


class FileWriteTool(ToolBase):
    """Write or append text to one file."""

    name = "file.write"
    description = "Write or append content to one file."
    parameters_model = FileWriteParams
    required_skill = "file-ops"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=FileWriteParams)
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
                must_exist=False,
            )
            if path.exists() and not path.is_file():
                raise ValueError(f"Path is not a file: {payload.path}")
            parent = path.parent
            if payload.create_dirs:
                parent.mkdir(parents=True, exist_ok=True)
            elif not parent.exists():
                raise ValueError(f"Parent directory does not exist: {parent}")

            preview_limit = min(self._settings.runtime_max_body_bytes, 32_768)
            if path.exists():
                previous_text, before_truncated, before_size_bytes = snapshot_path_text(
                    path=path,
                    max_bytes=preview_limit,
                )
            else:
                previous_text, before_truncated, before_size_bytes = "", False, 0
            write_mode = "a" if payload.mode == "append" else "w"
            with path.open(write_mode, encoding="utf-8") as handle:
                handle.write(payload.content)
            after_text, after_truncated, after_size_bytes = snapshot_path_text(
                path=path,
                max_bytes=preview_limit,
            )
            result_payload: dict[str, object] = {
                "path": to_workspace_relative(base_dir=base_dir, path=path),
                "mode": payload.mode,
                "bytes_written": len(payload.content.encode("utf-8")),
                "before_text": previous_text,
                "before_truncated": before_truncated,
                "before_size_bytes": before_size_bytes,
                "after_text": after_text,
                "after_truncated": after_truncated,
                "after_size_bytes": after_size_bytes,
            }
            if not before_truncated and not after_truncated:
                result_payload["diff_suggestion"] = {
                    "before": previous_text,
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
            return ToolResult.error(error_code="file_write_invalid", reason=str(exc))
        except OSError as exc:
            return ToolResult.error(error_code="file_write_failed", reason=f"{exc.__class__.__name__}: {exc}")


def create_tool(settings: Settings) -> ToolBase:
    """Create file.write tool instance."""

    return FileWriteTool(settings=settings)
