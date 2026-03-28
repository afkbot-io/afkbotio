"""Tool plugin for file.search."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.services.tools.text_snapshots import read_prefix_bytes
from afkbot.services.tools.workspace import (
    resolve_io_path,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    to_workspace_relative,
    truncate_utf8,
)
from afkbot.settings import Settings


class FileSearchParams(RoutedToolParameters):
    """Parameters for file.search tool."""

    path: str = Field(default=".", min_length=1, max_length=4096)
    query: str = Field(min_length=1, max_length=512)
    glob: str = Field(default="**/*", min_length=1, max_length=256)
    case_sensitive: bool = False
    max_results: int = Field(default=50, ge=1, le=500)
    max_bytes_per_file: int = Field(default=65536, ge=1, le=1_000_000)


class FileSearchTool(ToolBase):
    """Search plain text files under one filesystem directory."""

    name = "file.search"
    description = "Search text in files and return matching lines."
    parameters_model = FileSearchParams
    required_skill = "file-ops"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        prepared = self._prepare_params(ctx=ctx, params=params, expected=FileSearchParams)
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
            FileSearchTool._validate_glob_pattern(payload.glob)

            max_bytes = min(payload.max_bytes_per_file, self._settings.runtime_max_body_bytes)
            matches = self._search(
                base_dir=base_dir,
                base=base,
                query=payload.query,
                glob_pattern=payload.glob,
                case_sensitive=payload.case_sensitive,
                max_results=payload.max_results,
                max_bytes_per_file=max_bytes,
            )
            return ToolResult(ok=True, payload={"matches": matches, "count": len(matches)})
        except ValueError as exc:
            return ToolResult.error(error_code="file_search_invalid", reason=str(exc))
        except OSError as exc:
            return ToolResult.error(error_code="file_search_failed", reason=f"{exc.__class__.__name__}: {exc}")

    @staticmethod
    def _search(
        *,
        base_dir: Path,
        base: Path,
        query: str,
        glob_pattern: str,
        case_sensitive: bool,
        max_results: int,
        max_bytes_per_file: int,
    ) -> list[dict[str, object]]:
        needle = query if case_sensitive else query.lower()
        results: list[dict[str, object]] = []

        for file_path in base.glob(glob_pattern):
            resolved = file_path.resolve(strict=False)
            if not resolved.is_file():
                continue
            raw = read_prefix_bytes(path=resolved, max_bytes=max_bytes_per_file)
            text, _ = truncate_utf8(raw=raw, max_bytes=max_bytes_per_file)
            for index, line in enumerate(text.splitlines(), start=1):
                hay = line if case_sensitive else line.lower()
                if needle not in hay:
                    continue
                results.append(
                    {
                        "path": to_workspace_relative(base_dir=base_dir, path=resolved),
                        "line": index,
                        "snippet": line[:400],
                    }
                )
                if len(results) >= max_results:
                    return results
        return results

    @staticmethod
    def _validate_glob_pattern(value: str) -> None:
        """Reject unsafe glob patterns that can escape base/workspace boundaries."""

        glob_path = Path(value)
        if glob_path.is_absolute():
            raise ValueError("Absolute glob patterns are not allowed")
        if any(part == ".." for part in glob_path.parts):
            raise ValueError("Glob pattern cannot contain '..'")


def create_tool(settings: Settings) -> ToolBase:
    """Create file.search tool instance."""

    return FileSearchTool(settings=settings)
