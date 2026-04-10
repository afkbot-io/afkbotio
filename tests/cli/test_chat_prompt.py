"""Tests for chat prompt completion and local REPL controls."""

from __future__ import annotations

from prompt_toolkit.document import Document

from afkbot.cli.commands.chat_repl_controls import handle_chat_repl_local_command
from afkbot.cli.commands.chat_repl_specs import (
    chat_repl_command_metadata,
    chat_repl_local_command_arguments,
    chat_repl_local_commands,
)
from afkbot.cli.presentation.chat_workspace.composer import ChatPromptCompleter
from afkbot.cli.presentation.chat_workspace.presenter import (
    build_chat_workspace_surface_state,
)
from afkbot.cli.presentation.chat_workspace.toolbar import (
    build_chat_workspace_status_line,
)
from afkbot.cli.presentation.chat_workspace.status import (
    status_text_for_chat_workspace,
    toolbar_text_for_chat_workspace,
)
from afkbot.services.chat_session.activity_state import ChatActivitySnapshot
from afkbot.services.chat_session.input_catalog import ChatInputCatalog
from afkbot.services.chat_session.plan_ledger import ChatPlanSnapshot, ChatPlanStep
from afkbot.services.chat_session.session_state import ChatReplSessionState


def test_chat_prompt_completer_suggests_commands_capabilities_and_files() -> None:
    """Chat prompt completion should cover local commands, capabilities, and file references."""

    # Arrange
    catalog = ChatInputCatalog(
        skill_names=("security-secrets",),
        subagent_names=("reviewer",),
        app_names=("telegram",),
        mcp_server_names=("alpha",),
        mcp_tool_names=("mcp.alpha.search",),
        file_paths=("bootstrap/AGENTS.md", "skills/local/SKILL.md"),
    )
    completer = ChatPromptCompleter(
        catalog_getter=lambda: catalog,
        local_commands=chat_repl_local_commands(),
        local_command_arguments=chat_repl_local_command_arguments(),
        local_command_metadata=chat_repl_command_metadata(),
    )

    # Act
    command_items = list(completer.get_completions(Document("//pl", cursor_position=4), None))
    activity_command_items = list(completer.get_completions(Document("//ac", cursor_position=4), None))
    slash_items = list(completer.get_completions(Document("/", cursor_position=1), None))
    slash_command_items = list(completer.get_completions(Document("/cap", cursor_position=4), None))
    plan_argument_items = list(completer.get_completions(Document("//plan o", cursor_position=8), None))
    thinking_argument_items = list(
        completer.get_completions(Document("//thinking v", cursor_position=12), None)
    )
    capability_items = list(completer.get_completions(Document("$sec", cursor_position=4), None))
    app_items = list(completer.get_completions(Document("$tel", cursor_position=4), None))
    mcp_items = list(completer.get_completions(Document("$alp", cursor_position=4), None))
    mcp_tool_items = list(completer.get_completions(Document("$mcp.a", cursor_position=6), None))
    file_items = list(completer.get_completions(Document("@./boot", cursor_position=7), None))
    fallback_file_items = list(completer.get_completions(Document("@AG", cursor_position=3), None))

    # Assert
    assert any(item.text == "//plan" for item in command_items)
    assert any(item.text == "//activity" for item in activity_command_items)
    assert any(item.text == "/capabilities" for item in slash_items)
    assert all(item.text != "/" for item in slash_items)
    assert any(item.text == "/capabilities" for item in slash_command_items)
    assert all(not item.text.startswith("//") for item in slash_command_items)
    assert any(item.text == "off" for item in plan_argument_items)
    assert any(item.text == "on" for item in plan_argument_items)
    assert any(item.text == "very_high" for item in thinking_argument_items)
    assert any(item.text == "$security-secrets" for item in capability_items)
    assert any(item.text == "$telegram" and item.display_meta_text == "app" for item in app_items)
    assert any(item.text == "$alpha" and item.display_meta_text == "mcp server" for item in mcp_items)
    assert any(item.text == "$mcp.alpha.search" and item.display_meta_text == "mcp tool" for item in mcp_tool_items)
    assert any(item.text == "@bootstrap/AGENTS.md" for item in file_items)
    assert any(item.text == "@bootstrap/AGENTS.md" for item in fallback_file_items)


def test_chat_prompt_completer_reserves_at_for_files_only() -> None:
    """`@` completion should stay file-only even when capability names share the prefix."""

    # Arrange
    catalog = ChatInputCatalog(
        skill_names=("reviewer",),
        subagent_names=(),
        app_names=(),
        mcp_server_names=(),
        file_paths=("notes/reviewer.md",),
    )
    completer = ChatPromptCompleter(
        catalog_getter=lambda: catalog,
        local_commands=chat_repl_local_commands(),
        local_command_arguments=None,
    )

    # Act
    items = list(completer.get_completions(Document("@rev", cursor_position=4), None))

    # Assert
    assert any(item.text == "@notes/reviewer.md" for item in items)
    assert all(item.display_meta_text == "file" for item in items)



def test_chat_prompt_completer_reads_latest_catalog_snapshot() -> None:
    """Prompt completion should use the latest catalog returned by the getter."""

    # Arrange
    catalog = ChatInputCatalog(
        skill_names=("security-secrets",),
        subagent_names=(),
        app_names=(),
        mcp_server_names=(),
        mcp_tool_names=(),
        file_paths=(),
    )
    completer = ChatPromptCompleter(
        catalog_getter=lambda: catalog,
        local_commands=chat_repl_local_commands(),
        local_command_arguments=None,
    )

    # Act
    before_refresh = list(completer.get_completions(Document("$rev", cursor_position=4), None))
    catalog = ChatInputCatalog(
        skill_names=("security-secrets",),
        subagent_names=("reviewer",),
        app_names=("imap",),
        mcp_server_names=("alpha",),
        mcp_tool_names=("mcp.alpha.search",),
        file_paths=("bootstrap/AGENTS.md",),
    )
    after_refresh = list(completer.get_completions(Document("$rev", cursor_position=4), None))

    # Assert
    assert before_refresh == []
    assert any(item.text == "$reviewer" for item in after_refresh)


def test_chat_prompt_completer_ignores_trailing_whitespace_after_complete_token() -> None:
    """Trailing whitespace after a complete token should not reopen replacement completions."""

    # Arrange
    catalog = ChatInputCatalog(
        skill_names=("security-secrets",),
        subagent_names=(),
        file_paths=(),
    )
    completer = ChatPromptCompleter(
        catalog_getter=lambda: catalog,
        local_commands=chat_repl_local_commands(),
        local_command_arguments=chat_repl_local_command_arguments(),
        local_command_metadata=chat_repl_command_metadata(),
    )

    # Act
    help_items = list(completer.get_completions(Document("//help ", cursor_position=7), None))
    plan_items = list(completer.get_completions(Document("//plan ", cursor_position=7), None))

    # Assert
    assert help_items == []
    assert any(item.text == "auto" for item in plan_items)
    assert any(item.text == "on" for item in plan_items)


def test_chat_repl_local_command_updates_session_state() -> None:
    """Local REPL commands should update planning/thinking state without running the agent."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level="medium",
        default_planning_mode="auto",
        default_thinking_level="medium",
    )

    # Act
    plan_result = handle_chat_repl_local_command("//plan off", state=state)
    thinking_result = handle_chat_repl_local_command("//thinking high", state=state)
    status_result = handle_chat_repl_local_command("//status", state=state)

    # Assert
    assert plan_result.consumed is True
    assert plan_result.message == "Planning mode updated to: off"
    assert thinking_result.consumed is True
    assert thinking_result.message == "Thinking level updated to: high"
    assert status_result.consumed is True
    assert "planning_mode: off" in (status_result.message or "")
    assert "thinking_level: high" in (status_result.message or "")


def test_chat_repl_local_commands_reject_extra_args_and_can_reset_to_defaults() -> None:
    """Planning and thinking controls should reject invalid syntax and support reset."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="on",
        thinking_level="high",
        default_planning_mode="auto",
        default_thinking_level="medium",
    )

    # Act
    plan_usage = handle_chat_repl_local_command("//plan on extra", state=state)
    thinking_usage = handle_chat_repl_local_command("//thinking high extra", state=state)
    plan_reset = handle_chat_repl_local_command("//plan default", state=state)
    thinking_reset = handle_chat_repl_local_command("//thinking default", state=state)

    # Assert
    assert plan_usage.message == "Usage: //plan off|auto|on|default|show|clear"
    assert thinking_usage.message == "Usage: //thinking low|medium|high|very_high|default"
    assert plan_reset.message == "Planning mode reset to: auto"
    assert thinking_reset.message == "Thinking level reset to: medium"

def test_chat_repl_local_command_rejects_invalid_planning_mode_without_crashing() -> None:
    """Invalid `//plan` values should return one usage error instead of raising."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    # Act
    result = handle_chat_repl_local_command("//plan nope", state=state)

    # Assert
    assert result.consumed is True
    assert result.message == "planning mode must be one of: off, auto, on, default, show, clear"


def test_chat_repl_local_command_ignores_bare_slash_without_crashing() -> None:
    """Bare slash input should stay safe when the palette is unavailable."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    # Act
    result = handle_chat_repl_local_command("/", state=state)

    # Assert
    assert result.consumed is False
    assert result.message is None


def test_chat_repl_local_command_can_show_and_clear_stored_plan() -> None:
    """Stored REPL plans should be visible and clearable without running the agent."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
        latest_plan=ChatPlanSnapshot(
            raw_text="1. Inspect\n2. Implement",
            steps=(
                ChatPlanStep(text="Inspect"),
                ChatPlanStep(text="Implement", completed=True),
            ),
        ),
        latest_plan_phase="executing",
        latest_activity=ChatActivitySnapshot(
            stage="tool_call",
            summary="tool: bash.exec",
            detail="cmd=pytest tests/cli",
            running=True,
        ),
    )

    # Act
    show_result = handle_chat_repl_local_command("//plan show", state=state)
    clear_result = handle_chat_repl_local_command("//plan clear", state=state)

    # Assert
    assert show_result.message == (
        "AFK Plan\n"
        "  status: executing\n"
        "  activity: tool: bash.exec · detail=cmd=pytest tests/cli · running=True\n"
        "  [ ] Inspect\n"
        "  [x] Implement"
    )
    assert clear_result.message == "Stored plan cleared."
    assert state.latest_plan is None
    assert state.latest_plan_phase is None


def test_chat_repl_status_and_activity_surface_latest_activity() -> None:
    """Toolbar and local controls should expose the latest activity summary."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level="medium",
        default_planning_mode="auto",
        default_thinking_level="medium",
        latest_activity=ChatActivitySnapshot(
            stage="tool_call",
            summary="tool: bash.exec",
            detail="cmd=pytest tests/cli",
            running=True,
        ),
    )

    # Act
    activity_result = handle_chat_repl_local_command("/activity", state=state)
    status_text = status_text_for_chat_workspace(state)
    surface_state = build_chat_workspace_surface_state(state)
    toolbar_text = toolbar_text_for_chat_workspace(state)

    # Assert
    assert activity_result.message == (
        "Latest activity\n"
        "- tool: bash.exec · detail=cmd=pytest tests/cli · running=True"
    )
    assert "activity: tool: bash.exec · detail=cmd=pytest tests/cli · running=True" in status_text
    assert surface_state.status_lines == ()
    assert toolbar_text.startswith("/ commands · $ capabilities · @ files")


def test_chat_workspace_status_line_formats_elapsed_time_and_activity(
    monkeypatch,
) -> None:
    """The working strip should mirror Codex-like elapsed-time phrasing."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level="medium",
        default_planning_mode="auto",
        default_thinking_level="medium",
        active_turn=True,
        active_turn_started_at=10.0,
        latest_activity=ChatActivitySnapshot(
            stage="tool_call",
            summary="tool: bash.exec",
            detail="cmd=pytest tests/cli",
            running=True,
        ),
        queued_messages=1,
    )
    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.toolbar.monotonic",
        lambda: 12.7,
    )

    # Act
    rendered = build_chat_workspace_status_line(state)

    # Assert
    assert rendered == (
        "• Working (2s • esc to interrupt) · calling tool: bash.exec · queued 1 message"
    )
    assert build_chat_workspace_surface_state(state).queue_lines == ("◦ Queued 1 message for the next turn.",)


def test_chat_workspace_surface_shows_active_plan_summary() -> None:
    """Workspace surface should show the active stored plan while execution is running."""

    state = ChatReplSessionState(
        planning_mode="on",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
        active_turn=True,
        latest_plan=ChatPlanSnapshot(
            raw_text="1. Inspect\n2. Implement\n3. Verify",
            steps=(
                ChatPlanStep(text="Inspect"),
                ChatPlanStep(text="Implement"),
                ChatPlanStep(text="Verify"),
            ),
        ),
        latest_plan_phase="executing",
    )

    surface_state = build_chat_workspace_surface_state(state)

    assert surface_state.queue_lines == ("◦ Plan executing · 3 step(s) · Inspect, Implement, ...",)


def test_chat_workspace_status_line_formats_minutes_and_hours(monkeypatch) -> None:
    """Elapsed labels should roll over from seconds to minutes and hours."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level="medium",
        default_planning_mode="auto",
        default_thinking_level="medium",
        active_turn=True,
        active_turn_started_at=10.0,
        latest_activity=ChatActivitySnapshot(
            stage="thinking",
            summary="thinking",
            running=True,
        ),
    )

    # Act / Assert
    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.toolbar.monotonic",
        lambda: 10.0 + 100,
    )
    assert build_chat_workspace_status_line(state).startswith("• Working (1m 40s • esc to interrupt)")

    monkeypatch.setattr(
        "afkbot.cli.presentation.chat_workspace.toolbar.monotonic",
        lambda: 10.0 + 3_700,
    )
    assert build_chat_workspace_status_line(state).startswith("• Working (1h 01m 40s • esc to interrupt)")



def test_chat_repl_help_mentions_inline_popup_and_aliases() -> None:
    """Local help should surface inline composer suggestions and slash aliases."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level="medium",
        default_planning_mode="auto",
        default_thinking_level="medium",
    )

    # Act
    help_result = handle_chat_repl_local_command("//help", state=state)

    # Assert
    assert help_result.consumed is True
    assert "type `/` to open inline command suggestions in the composer" in (help_result.message or "")
    assert "slash aliases are also available: /help, /status, /activity" in (help_result.message or "")



def test_chat_repl_capabilities_command_renders_catalog_sections() -> None:
    """Capability catalog controls should expose skills, subagents, apps, and MCP hints."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
        latest_catalog=ChatInputCatalog(
            skill_names=("security-secrets",),
            subagent_names=("reviewer",),
            app_names=("telegram",),
            mcp_server_names=("alpha",),
            file_paths=("bootstrap/AGENTS.md",),
        ),
    )

    # Act
    all_result = handle_chat_repl_local_command("//capabilities", state=state)
    mcp_result = handle_chat_repl_local_command("//capabilities mcp", state=state)

    # Assert
    assert "- skills: security-secrets" in (all_result.message or "")
    assert "- subagents: reviewer" in (all_result.message or "")
    assert "- apps: telegram" in (all_result.message or "")
    assert "- mcp_servers: alpha" in (mcp_result.message or "")
    assert "mcp.tools.list" in (mcp_result.message or "")
