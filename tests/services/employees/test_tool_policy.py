"""Tests for Task Flow employee tool access policy."""

from afkbot.services.employees.tool_policy import _tool_name_allowed


def test_employee_tool_policy_allows_star_wildcard() -> None:
    """Employee `*` access should allow every current and future tool name."""

    assert _tool_name_allowed(tool_name="task.create", allowed_tools=("*",)) is True
    assert _tool_name_allowed(tool_name="bash.exec", allowed_tools=("*",)) is True


def test_employee_tool_policy_matches_exact_and_prefix_groups() -> None:
    """Employee selectable groups should allow exact tools and bounded `prefix.*` groups."""

    allowed_tools = ("task.*", "file.read", "mcp.tools.call")

    assert _tool_name_allowed(tool_name="task.comment.add", allowed_tools=allowed_tools) is True
    assert _tool_name_allowed(tool_name="file.read", allowed_tools=allowed_tools) is True
    assert _tool_name_allowed(tool_name="mcp.tools.call", allowed_tools=allowed_tools) is True
    assert _tool_name_allowed(tool_name="taskflow.create", allowed_tools=allowed_tools) is False
    assert _tool_name_allowed(tool_name="file.write", allowed_tools=allowed_tools) is False
