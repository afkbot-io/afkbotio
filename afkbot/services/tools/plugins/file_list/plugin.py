"""Tool plugin for file.list."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.services.tools.workspace import (
    resolve_io_path,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    to_workspace_relative,
)
from afkbot.settings import Settings


class FileListParams(RoutedToolParameters):
    """Parameters for file.list tool."""

    path: str = Field(default=".", min_length=1, max_length=4096)
    recursive: bool = False
    include_hidden: bool = False
    max_entries: int = Field(default=200, ge=1, le=1000)


class FileListTool(ToolBase):
    """List files/directories inside one filesystem directory."""

    name = "file.list"
    description = "List files and directories."
    parameters_model = FileListParams
    required_skill = "file-ops"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=FileListParams)
        if isinstance(prepared, ToolResult):
            return prepared
        payload = prepared

        try:
            base_dir = resolve_tool_workspace_base_dir(settings=self._settings, profile_id=ctx.profile_id)
            scope_roots = await resolve_tool_workspace_scope_roots(
                settings=self._settings,
                profile_id=ctx.profile_id,
            )
            base = resolve_io_path(
                base_dir=base_dir,
                scope_roots=scope_roots,
                raw_path=payload.path,
                must_exist=True,
            )
            if not base.is_dir():
                raise ValueError(f"Path is not a directory: {payload.path}")

            entries = self._collect_entries(
                base_dir=base_dir,
                base=base,
                recursive=payload.recursive,
                include_hidden=payload.include_hidden,
                max_entries=payload.max_entries,
            )
            return ToolResult(
                ok=True,
                payload={"base_path": to_workspace_relative(base_dir=base_dir, path=base), "entries": entries},
            )
        except ValueError as exc:
            return ToolResult.error(error_code="file_list_invalid", reason=str(exc))

    @staticmethod
    def _collect_entries(
        *,
        base_dir: Path,
        base: Path,
        recursive: bool,
        include_hidden: bool,
        max_entries: int,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        iterator = base.rglob("*") if recursive else base.iterdir()
        for item in iterator:
            relative = item.relative_to(base).as_posix()
            if not include_hidden and any(part.startswith(".") for part in relative.split("/")):
                continue
            results.append(
                {
                    "path": to_workspace_relative(base_dir=base_dir, path=item),
                    "kind": "dir" if item.is_dir() else "file",
                }
            )
            if len(results) >= max_entries:
                break
        return results


def create_tool(settings: Settings) -> ToolBase:
    """Create file.list tool instance."""

    return FileListTool(settings=settings)
