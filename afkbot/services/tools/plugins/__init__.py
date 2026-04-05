"""Built-in tool plugin factories."""

from __future__ import annotations

from collections.abc import Callable

from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.plugins.automation_create import create_tool as create_automation_create
from afkbot.services.tools.plugins.automation_delete import create_tool as create_automation_delete
from afkbot.services.tools.plugins.automation_get import create_tool as create_automation_get
from afkbot.services.tools.plugins.automation_list import create_tool as create_automation_list
from afkbot.services.tools.plugins.automation_update import create_tool as create_automation_update
from afkbot.services.tools.plugins.bash_exec import create_tool as create_bash_exec_tool
from afkbot.services.tools.plugins.browser_control import create_tool as create_browser_control_tool
from afkbot.services.tools.plugins.credentials_create import (
    create_tool as create_credentials_create,
)
from afkbot.services.tools.plugins.credentials_delete import (
    create_tool as create_credentials_delete,
)
from afkbot.services.tools.plugins.credentials_list import create_tool as create_credentials_list
from afkbot.services.tools.plugins.credentials_request import (
    create_tool as create_credentials_request,
)
from afkbot.services.tools.plugins.credentials_update import (
    create_tool as create_credentials_update,
)
from afkbot.services.tools.plugins.debug_echo import create_tool as create_debug_echo_tool
from afkbot.services.tools.plugins.diffs_render import create_tool as create_diffs_render_tool
from afkbot.services.tools.plugins.file_edit import create_tool as create_file_edit_tool
from afkbot.services.tools.plugins.file_list import create_tool as create_file_list_tool
from afkbot.services.tools.plugins.file_read import create_tool as create_file_read_tool
from afkbot.services.tools.plugins.file_search import create_tool as create_file_search_tool
from afkbot.services.tools.plugins.file_write import create_tool as create_file_write_tool
from afkbot.services.tools.plugins.http_request import create_tool as create_http_request_tool
from afkbot.services.tools.plugins.memory_delete import create_tool as create_memory_delete_tool
from afkbot.services.tools.plugins.memory_digest import create_tool as create_memory_digest_tool
from afkbot.services.tools.plugins.memory_list import create_tool as create_memory_list_tool
from afkbot.services.tools.plugins.memory_promote import create_tool as create_memory_promote_tool
from afkbot.services.tools.plugins.memory_search import create_tool as create_memory_search_tool
from afkbot.services.tools.plugins.memory_upsert import create_tool as create_memory_upsert_tool
from afkbot.services.tools.plugins.mcp_profile_delete import create_tool as create_mcp_profile_delete
from afkbot.services.tools.plugins.mcp_profile_get import create_tool as create_mcp_profile_get
from afkbot.services.tools.plugins.mcp_profile_list import create_tool as create_mcp_profile_list
from afkbot.services.tools.plugins.mcp_profile_upsert import create_tool as create_mcp_profile_upsert
from afkbot.services.tools.plugins.mcp_profile_validate import (
    create_tool as create_mcp_profile_validate,
)
from afkbot.services.tools.plugins.skill_profile_delete import create_tool as create_skill_profile_delete
from afkbot.services.tools.plugins.skill_profile_get import create_tool as create_skill_profile_get
from afkbot.services.tools.plugins.skill_profile_list import create_tool as create_skill_profile_list
from afkbot.services.tools.plugins.skill_profile_upsert import create_tool as create_skill_profile_upsert
from afkbot.services.tools.plugins.skill_marketplace import (
    create_install_tool as create_skill_marketplace_install_tool,
)
from afkbot.services.tools.plugins.skill_marketplace import (
    create_list_tool as create_skill_marketplace_list_tool,
)
from afkbot.services.tools.plugins.skill_marketplace import (
    create_search_tool as create_skill_marketplace_search_tool,
)
from afkbot.services.tools.plugins.subagent_profile_delete import (
    create_tool as create_subagent_profile_delete,
)
from afkbot.services.tools.plugins.subagent_profile_get import (
    create_tool as create_subagent_profile_get,
)
from afkbot.services.tools.plugins.subagent_profile_list import (
    create_tool as create_subagent_profile_list,
)
from afkbot.services.tools.plugins.subagent_profile_upsert import (
    create_tool as create_subagent_profile_upsert,
)
from afkbot.services.tools.plugins.subagent_result import create_tool as create_subagent_result_tool
from afkbot.services.tools.plugins.subagent_run import create_tool as create_subagent_run_tool
from afkbot.services.tools.plugins.subagent_wait import create_tool as create_subagent_wait_tool
from afkbot.services.tools.plugins.task_board import create_tool as create_task_board_tool
from afkbot.services.tools.plugins.task_comment_add import create_tool as create_task_comment_add_tool
from afkbot.services.tools.plugins.task_comment_list import create_tool as create_task_comment_list_tool
from afkbot.services.tools.plugins.task_create import create_tool as create_task_create_tool
from afkbot.services.tools.plugins.task_dependency_add import create_tool as create_task_dependency_add_tool
from afkbot.services.tools.plugins.task_dependency_list import create_tool as create_task_dependency_list_tool
from afkbot.services.tools.plugins.task_dependency_remove import create_tool as create_task_dependency_remove_tool
from afkbot.services.tools.plugins.task_event_list import create_tool as create_task_event_list_tool
from afkbot.services.tools.plugins.task_flow_create import create_tool as create_task_flow_create_tool
from afkbot.services.tools.plugins.task_flow_get import create_tool as create_task_flow_get_tool
from afkbot.services.tools.plugins.task_flow_list import create_tool as create_task_flow_list_tool
from afkbot.services.tools.plugins.task_get import create_tool as create_task_get_tool
from afkbot.services.tools.plugins.task_inbox import create_tool as create_task_inbox_tool
from afkbot.services.tools.plugins.task_list import create_tool as create_task_list_tool
from afkbot.services.tools.plugins.task_maintenance_sweep import (
    create_tool as create_task_maintenance_sweep_tool,
)
from afkbot.services.tools.plugins.task_review_approve import create_tool as create_task_review_approve_tool
from afkbot.services.tools.plugins.task_review_list import create_tool as create_task_review_list_tool
from afkbot.services.tools.plugins.task_review_request_changes import (
    create_tool as create_task_review_request_changes_tool,
)
from afkbot.services.tools.plugins.task_run_get import create_tool as create_task_run_get_tool
from afkbot.services.tools.plugins.task_run_list import create_tool as create_task_run_list_tool
from afkbot.services.tools.plugins.task_stale_list import create_tool as create_task_stale_list_tool
from afkbot.services.tools.plugins.task_update import create_tool as create_task_update_tool
from afkbot.services.tools.plugins.web_fetch import create_tool as create_web_fetch_tool
from afkbot.services.tools.plugins.web_search import create_tool as create_web_search_tool
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings

try:
    from afkbot.services.tools.plugins.app_list import create_tool as create_app_list_tool
except Exception as exc:  # pragma: no cover - environment-dependent fallback
    _APP_LIST_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"

    class _UnavailableAppListTool(ToolBase):
        """Fallback app.list tool when optional app runtime dependencies are unavailable."""

        name = "app.list"
        description = "app.list integration is unavailable in current runtime build."

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            _ = ctx, params
            return ToolResult.error(
                error_code="app_list_failed",
                reason=f"app.list plugin is unavailable ({_APP_LIST_IMPORT_ERROR})",
            )

    def create_app_list_tool(settings: Settings) -> ToolBase:
        _ = settings
        return _UnavailableAppListTool()


try:
    from afkbot.services.tools.plugins.app_run import create_tool as create_app_run_tool
except Exception as exc:  # pragma: no cover - environment-dependent fallback
    _APP_RUN_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"

    class _UnavailableAppRunTool(ToolBase):
        """Fallback app.run tool when optional app runtime dependencies are unavailable."""

        name = "app.run"
        description = "app.run integration is unavailable in current runtime build."

        async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
            _ = ctx, params
            return ToolResult.error(
                error_code="app_run_failed",
                reason=f"app.run plugin is unavailable ({_APP_RUN_IMPORT_ERROR})",
            )

    def create_app_run_tool(settings: Settings) -> ToolBase:
        _ = settings
        return _UnavailableAppRunTool()


_PLUGIN_FACTORIES: dict[str, Callable[[Settings], ToolBase]] = {
    "app_list": create_app_list_tool,
    "app_run": create_app_run_tool,
    "bash_exec": create_bash_exec_tool,
    "browser_control": create_browser_control_tool,
    "automation_create": create_automation_create,
    "automation_list": create_automation_list,
    "automation_get": create_automation_get,
    "automation_update": create_automation_update,
    "automation_delete": create_automation_delete,
    "credentials_create": create_credentials_create,
    "credentials_update": create_credentials_update,
    "credentials_delete": create_credentials_delete,
    "credentials_list": create_credentials_list,
    "credentials_request": create_credentials_request,
    "debug_echo": create_debug_echo_tool,
    "diffs_render": create_diffs_render_tool,
    "file_list": create_file_list_tool,
    "file_read": create_file_read_tool,
    "file_write": create_file_write_tool,
    "file_edit": create_file_edit_tool,
    "file_search": create_file_search_tool,
    "http_request": create_http_request_tool,
    "memory_upsert": create_memory_upsert_tool,
    "memory_search": create_memory_search_tool,
    "memory_delete": create_memory_delete_tool,
    "memory_digest": create_memory_digest_tool,
    "memory_list": create_memory_list_tool,
    "memory_promote": create_memory_promote_tool,
    "mcp_profile_list": create_mcp_profile_list,
    "mcp_profile_get": create_mcp_profile_get,
    "mcp_profile_upsert": create_mcp_profile_upsert,
    "mcp_profile_delete": create_mcp_profile_delete,
    "mcp_profile_validate": create_mcp_profile_validate,
    "skill_profile_list": create_skill_profile_list,
    "skill_profile_get": create_skill_profile_get,
    "skill_profile_upsert": create_skill_profile_upsert,
    "skill_profile_delete": create_skill_profile_delete,
    "skill_marketplace_list": create_skill_marketplace_list_tool,
    "skill_marketplace_search": create_skill_marketplace_search_tool,
    "skill_marketplace_install": create_skill_marketplace_install_tool,
    "subagent_run": create_subagent_run_tool,
    "subagent_wait": create_subagent_wait_tool,
    "subagent_result": create_subagent_result_tool,
    "task_board": create_task_board_tool,
    "task_comment_add": create_task_comment_add_tool,
    "task_comment_list": create_task_comment_list_tool,
    "task_create": create_task_create_tool,
    "task_dependency_add": create_task_dependency_add_tool,
    "task_dependency_list": create_task_dependency_list_tool,
    "task_dependency_remove": create_task_dependency_remove_tool,
    "task_event_list": create_task_event_list_tool,
    "task_flow_create": create_task_flow_create_tool,
    "task_flow_get": create_task_flow_get_tool,
    "task_flow_list": create_task_flow_list_tool,
    "task_get": create_task_get_tool,
    "task_inbox": create_task_inbox_tool,
    "task_list": create_task_list_tool,
    "task_maintenance_sweep": create_task_maintenance_sweep_tool,
    "task_review_approve": create_task_review_approve_tool,
    "task_review_list": create_task_review_list_tool,
    "task_review_request_changes": create_task_review_request_changes_tool,
    "task_run_get": create_task_run_get_tool,
    "task_run_list": create_task_run_list_tool,
    "task_stale_list": create_task_stale_list_tool,
    "task_update": create_task_update_tool,
    "subagent_profile_list": create_subagent_profile_list,
    "subagent_profile_get": create_subagent_profile_get,
    "subagent_profile_upsert": create_subagent_profile_upsert,
    "subagent_profile_delete": create_subagent_profile_delete,
    "web_fetch": create_web_fetch_tool,
    "web_search": create_web_search_tool,
}


def create_tool_from_plugin(plugin_name: str, settings: Settings) -> ToolBase:
    """Instantiate tool for one configured plugin name."""

    try:
        factory = _PLUGIN_FACTORIES[plugin_name]
    except KeyError as exc:
        raise ValueError(f"Unknown tool plugin: {plugin_name}") from exc
    return factory(settings)


def list_available_plugins() -> tuple[str, ...]:
    """List built-in plugin identifiers accepted by settings."""

    return tuple(sorted(_PLUGIN_FACTORIES.keys()))


__all__ = ["create_tool_from_plugin", "list_available_plugins"]
