"""Tool plugin for file.read."""

from __future__ import annotations

import asyncio

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


class FileReadParams(RoutedToolParameters):
    """Parameters for file.read tool."""

    path: str = Field(min_length=1, max_length=4096)
    max_bytes: int = Field(default=65536, ge=1, le=1_000_000)


class FileReadTool(ToolBase):
    """Read file content from filesystem with deterministic truncation."""

    name = "file.read"
    description = "Read one file with bounded output size."
    parameters_model = FileReadParams
    required_skill = "file-ops"
    parallel_execution_safe = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=FileReadParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        try:
            base_dir = resolve_tool_workspace_base_dir(
                settings=self._settings, profile_id=ctx.profile_id
            )
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

            max_bytes = min(payload.max_bytes, self._settings.runtime_max_body_bytes)
            content, truncated, size_bytes = await asyncio.to_thread(
                snapshot_path_text,
                path=path,
                max_bytes=max_bytes,
            )
            return ToolResult(
                ok=True,
                payload={
                    "path": to_workspace_relative(base_dir=base_dir, path=path),
                    "content": content,
                    "truncated": truncated,
                    "size_bytes": size_bytes,
                },
            )
        except ValueError as exc:
            return ToolResult.error(error_code="file_read_invalid", reason=str(exc))
        except OSError as exc:
            return ToolResult.error(
                error_code="file_read_failed", reason=f"{exc.__class__.__name__}: {exc}"
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create file.read tool instance."""

    return FileReadTool(settings=settings)
