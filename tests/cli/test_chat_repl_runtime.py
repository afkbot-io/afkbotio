"""Tests for chat REPL input handling helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.cli.commands.chat_task_startup_digest import (
    compose_human_task_startup_message,
    render_human_task_startup_summary,
)
from afkbot.cli.commands.chat_repl_runtime import _run_repl_sequential, run_repl_transport
from afkbot.cli.commands.chat_repl_input import consume_chat_repl_input
from afkbot.services.chat_session.input_catalog import ChatInputCatalog, ChatInputCatalogStore
from afkbot.services.chat_session.repl_input import ChatReplInputOutcome
from afkbot.services.chat_session.repl_queue import ChatReplQueueFullError, ChatReplTurnQueue
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnInteractiveOptions, ChatTurnOutcome
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.llm.contracts import LLMResponse
from afkbot.services.llm.mock_provider import MockLLMProvider
from afkbot.services.task_flow import (
    HumanTaskInboxEventMetadata,
    HumanTaskInboxMetadata,
    HumanTaskStartupSummary,
    TaskMetadata,
)
from afkbot.settings import Settings


def test_consume_chat_repl_input_queues_follow_up_message() -> None:
    """Active-turn input should enter the FIFO queue and expose one queue notice."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_queue = ChatReplTurnQueue()

    # Act
    outcome = consume_chat_repl_input(
        raw_message="follow up",
        repl_state=state,
        turn_queue=turn_queue,
        turn_active=True,
    )

    # Assert
    assert outcome.consumed is True
    assert outcome.exit_repl is False
    assert outcome.message is None
    assert outcome.notice == "Queued next message. Pending queue: 1"
    assert outcome.queued_message == "follow up"
    assert state.queued_messages == 1
    assert turn_queue.size == 1


def test_consume_chat_repl_input_rejects_follow_up_when_queue_is_full() -> None:
    """Active-turn input should not grow the follow-up queue without bound."""

    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_queue = ChatReplTurnQueue()
    for index in range(turn_queue.max_size):
        turn_queue.enqueue(f"queued {index}")

    outcome = consume_chat_repl_input(
        raw_message="one too many",
        repl_state=state,
        turn_queue=turn_queue,
        turn_active=True,
    )

    assert outcome.consumed is True
    assert outcome.queued_message is None
    assert outcome.notice == (
        f"Message not queued. Pending queue is full ({turn_queue.max_size}). "
        "Wait for the current turn or use //cancel."
    )
    assert state.queued_messages == turn_queue.max_size
    assert turn_queue.size == turn_queue.max_size
    try:
        turn_queue.enqueue("still too many")
    except ChatReplQueueFullError:
        pass
    else:
        raise AssertionError("full chat queue accepted another message")


def test_consume_chat_repl_input_cancel_clears_queue_and_requests_turn_cancel() -> None:
    """The local cancel command should abort the active turn instead of queueing text."""

    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
        queued_messages=1,
    )
    turn_queue = ChatReplTurnQueue()
    turn_queue.enqueue("stale follow up")

    outcome = consume_chat_repl_input(
        raw_message="//cancel",
        repl_state=state,
        turn_queue=turn_queue,
        turn_active=True,
    )

    assert outcome.consumed is True
    assert outcome.cancel_active_turn is True
    assert outcome.notice == "Cancelling current turn. Pending queue cleared."
    assert outcome.queued_message is None
    assert state.queued_messages == 0
    assert turn_queue.size == 0


def test_consume_chat_repl_input_returns_local_command_message_in_sequential_mode() -> None:
    """Sequential-mode local commands should update state without becoming agent input."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_queue = ChatReplTurnQueue()

    # Act
    outcome = consume_chat_repl_input(
        raw_message="//plan off",
        repl_state=state,
        turn_queue=turn_queue,
        turn_active=False,
        queue_messages=False,
    )

    # Assert
    assert outcome.consumed is True
    assert outcome.exit_repl is False
    assert outcome.message == "Planning mode updated to: off"
    assert outcome.notice is None
    assert outcome.queued_message is None
    assert state.planning_mode == "off"
    assert turn_queue.size == 0


def test_consume_chat_repl_input_keeps_normal_message_unqueued_in_sequential_mode() -> None:
    """Sequential-mode input should not enqueue the message before the direct turn call."""

    # Arrange
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )
    turn_queue = ChatReplTurnQueue()

    # Act
    outcome = consume_chat_repl_input(
        raw_message="run directly",
        repl_state=state,
        turn_queue=turn_queue,
        turn_active=False,
        queue_messages=False,
    )

    # Assert
    assert outcome.consumed is False
    assert outcome.exit_repl is False
    assert outcome.message is None
    assert outcome.notice is None
    assert outcome.queued_message is None
    assert state.queued_messages == 0
    assert turn_queue.size == 0


def test_run_repl_transport_routes_interactive_tty_into_fullscreen_workspace(
    monkeypatch,
) -> None:
    """Interactive TTY sessions should delegate to the fullscreen workspace runtime."""

    # Arrange
    initial_catalog = ChatInputCatalog(
        skill_names=("review",),
        subagent_names=(),
    )
    refreshed_catalog = ChatInputCatalog(
        skill_names=("review",),
        subagent_names=("qa",),
    )
    catalog_store = ChatInputCatalogStore(initial_catalog)
    captured: dict[str, object] = {}

    class _FakeBrowserSessionManager:
        async def close_session(
            self,
            *,
            root_dir,
            profile_id: str,
            session_id: str,
        ) -> None:
            captured["closed"] = (str(root_dir), profile_id, session_id)

    async def _fake_refresh_catalog() -> None:
        catalog_store.replace(refreshed_catalog)

    async def _fake_fullscreen_session(
        *,
        profile_id: str,
        session_id: str,
        repl_state,
        catalog_getter,
        refresh_catalog,
        run_turn,
        startup_assistant_message=None,
    ) -> None:
        _ = repl_state, run_turn, startup_assistant_message
        captured["profile_id"] = profile_id
        captured["session_id"] = session_id
        captured["catalog_before"] = catalog_getter()
        await refresh_catalog()
        captured["catalog_after"] = catalog_getter()

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_repl_runtime.supports_interactive_tty",
        lambda: True,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_repl_runtime.build_chat_workspace_catalog_store",
        lambda **_: catalog_store,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_repl_runtime.build_chat_workspace_catalog_refresher",
        lambda **_: _fake_refresh_catalog,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.chat_repl_runtime.run_fullscreen_chat_workspace_session",
        _fake_fullscreen_session,
    )

    async def _unused_run_turn(*_args, **_kwargs):
        raise AssertionError("run_turn should not execute in transport routing test")

    settings = Settings(root_dir=".")
    browser_manager = _FakeBrowserSessionManager()

    # Act
    run_repl_transport(
        profile_id="default",
        session_id="session-1",
        run_turn=_unused_run_turn,
        get_browser_session_manager=lambda: browser_manager,
        get_settings=lambda: settings,
        planning_mode="auto",
        thinking_level=None,
    )

    # Assert
    assert captured["profile_id"] == "default"
    assert captured["session_id"] == "session-1"
    assert captured["catalog_before"] == initial_catalog
    assert captured["catalog_after"] == refreshed_catalog
    assert captured["closed"] == (str(settings.root_dir), "default", "session-1")


def test_run_repl_sequential_reuses_one_queue_across_inputs(monkeypatch) -> None:
    """Sequential REPL should keep one queue instance for the entire session."""

    # Arrange
    queue_ids: list[int] = []
    seen_messages: list[str] = []
    state = ChatReplSessionState(
        planning_mode="auto",
        thinking_level=None,
        default_planning_mode="auto",
        default_thinking_level=None,
    )

    class _FakeRunner:
        def run(self, coro):
            return asyncio.run(coro)

    class _FakeUX:
        def __init__(self) -> None:
            self._messages = iter(("hello", "//quit"))

        def read_user_input(self) -> str:
            return next(self._messages)

        def begin_agent_turn(self) -> None:
            return None

        def stop_progress(self) -> None:
            return None

    def _fake_consume_chat_repl_input(
        *,
        raw_message: str,
        repl_state: ChatReplSessionState,
        turn_queue: ChatReplTurnQueue,
        turn_active: bool,
        queue_messages: bool = True,
    ) -> ChatReplInputOutcome:
        _ = repl_state, turn_active, queue_messages
        queue_ids.append(id(turn_queue))
        if raw_message == "//quit":
            return ChatReplInputOutcome(consumed=True, exit_repl=True)
        return ChatReplInputOutcome(consumed=False)

    async def _fake_run_turn(
        message: str,
        progress_sink,
        repl_state: ChatReplSessionState,
        turn_options: ChatTurnInteractiveOptions,
    ) -> ChatTurnOutcome:
        _ = progress_sink, repl_state, turn_options
        seen_messages.append(message)
        return ChatTurnOutcome(
            result=TurnResult(
                run_id=1,
                session_id="s-sequential",
                profile_id="default",
                envelope=ActionEnvelope(action="finalize", message="done"),
            )
        )

    monkeypatch.setattr(
        "afkbot.cli.commands.chat_repl_runtime.consume_chat_repl_input",
        _fake_consume_chat_repl_input,
    )

    # Act
    _run_repl_sequential(
        runner=_FakeRunner(),
        ux=_FakeUX(),
        profile_id="default",
        session_id="s-sequential",
        run_turn=_fake_run_turn,
        repl_state=state,
        progress_sink=lambda _event: None,
        refresh_catalog=lambda: _async_noop(),
    )

    # Assert
    assert seen_messages == ["hello"]
    assert len(queue_ids) == 2
    assert len(set(queue_ids)) == 1


def test_render_human_task_startup_summary_renders_ru_notice(monkeypatch) -> None:
    """Human startup summary should render task titles and localized summary copy."""

    from afkbot.cli.commands import chat_task_startup_digest as module

    monkeypatch.setattr(module, "resolve_prompt_language", lambda **kwargs: PromptLanguage.RU)
    summary = HumanTaskStartupSummary(
        owner_ref="cli_user:alice",
        total_count=2,
        todo_count=1,
        blocked_count=1,
        review_count=0,
        overdue_count=1,
        tasks=(
            TaskMetadata(
                id="task_1",
                profile_id="default",
                flow_id=None,
                title="Подготовить релиз",
                prompt="Собрать changelog",
                status="todo",
                priority=70,
                due_at=datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc),
                ready_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
                owner_type="human",
                owner_ref="cli_user:alice",
                reviewer_type=None,
                reviewer_ref=None,
                source_type="manual",
                source_ref=None,
                created_by_type="human",
                created_by_ref="cli",
                labels=("release",),
                requires_review=False,
                blocked_reason_code=None,
                blocked_reason_text=None,
                current_attempt=0,
                last_session_id=None,
                last_run_id=None,
                last_error_code=None,
                last_error_text=None,
                started_at=None,
                finished_at=None,
                created_at=datetime(2026, 4, 5, 10, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 5, 10, 5, tzinfo=timezone.utc),
            ),
            TaskMetadata(
                id="task_2",
                profile_id="default",
                flow_id=None,
                title="Дождаться дизайна",
                prompt="Ждём финальный макет",
                status="blocked",
                priority=50,
                due_at=None,
                ready_at=None,
                owner_type="human",
                owner_ref="cli_user:alice",
                reviewer_type=None,
                reviewer_ref=None,
                source_type="manual",
                source_ref=None,
                created_by_type="human",
                created_by_ref="cli",
                labels=(),
                requires_review=False,
                blocked_reason_code="dependency_wait",
                blocked_reason_text="Waiting",
                current_attempt=0,
                last_session_id=None,
                last_run_id=None,
                last_error_code=None,
                last_error_text=None,
                started_at=None,
                finished_at=None,
                created_at=datetime(2026, 4, 5, 11, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 4, 5, 11, 5, tzinfo=timezone.utc),
            ),
        ),
    )

    rendered = render_human_task_startup_summary(summary)

    assert rendered is not None
    assert "Для вас есть 2 открытых задач" in rendered
    assert "Подготовить релиз" in rendered
    assert "Дождаться дизайна" in rendered
    assert "просрочено: 1" in rendered
    assert "Используйте `afk task inbox`" in rendered


def test_render_human_task_startup_summary_includes_recent_inbox_activity(monkeypatch) -> None:
    """Startup digest should prepend recent inbox activity when provided."""

    from afkbot.cli.commands import chat_task_startup_digest as module

    monkeypatch.setattr(module, "resolve_prompt_language", lambda **kwargs: PromptLanguage.RU)
    summary = HumanTaskStartupSummary(
        owner_ref="cli_user:alice",
        total_count=1,
        todo_count=0,
        blocked_count=0,
        review_count=1,
        overdue_count=0,
        tasks=(),
    )
    inbox = HumanTaskInboxMetadata(
        owner_ref="cli_user:alice",
        channel="chat_startup",
        total_count=1,
        todo_count=0,
        blocked_count=0,
        review_count=1,
        overdue_count=0,
        unseen_event_count=1,
        tasks=(),
        recent_events=(
            HumanTaskInboxEventMetadata(
                id=5,
                task_id="task_5",
                task_title="Проверить ответ",
                event_type="execution_review_ready",
                actor_type="runtime",
                actor_ref="worker-a",
                message="Готово к ревью",
                from_status="running",
                to_status="review",
                details={"run_id": 7},
                created_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    rendered = render_human_task_startup_summary(summary, inbox=inbox)

    assert rendered is not None
    assert "С прошлого чата появились новые изменения" in rendered
    assert "Проверить ответ: готово к ревью" in rendered
    assert "Для вас есть 1 открытых задач" in rendered


async def test_compose_human_task_startup_message_prefers_llm_digest(monkeypatch) -> None:
    """Startup digest should prefer concise LLM output when available."""

    from afkbot.cli.commands import chat_task_startup_digest as module

    monkeypatch.setattr(module, "resolve_prompt_language", lambda **kwargs: PromptLanguage.RU)
    monkeypatch.setattr(module, "resolve_profile_settings", lambda **kwargs: kwargs["settings"])
    provider = MockLLMProvider(
        [LLMResponse.final("Для вас 2 задачи: одна на review, одна в todo. Начните с review.")]
    )
    monkeypatch.setattr(module, "build_llm_provider", lambda _settings: provider)

    summary = HumanTaskStartupSummary(
        owner_ref="cli_user:alice",
        total_count=2,
        todo_count=1,
        blocked_count=0,
        review_count=1,
        overdue_count=0,
        tasks=(),
    )

    rendered = await compose_human_task_startup_message(
        settings=Settings(root_dir="."),
        profile_id="default",
        summary=summary,
    )

    assert rendered == "Для вас 2 задачи: одна на review, одна в todo. Начните с review."
    assert provider.requests


async def test_compose_human_task_startup_message_falls_back_when_provider_errors(
    monkeypatch,
) -> None:
    """Startup digest should fall back to deterministic copy on provider failure."""

    from afkbot.cli.commands import chat_task_startup_digest as module

    monkeypatch.setattr(module, "resolve_prompt_language", lambda **kwargs: PromptLanguage.RU)
    monkeypatch.setattr(module, "resolve_profile_settings", lambda **kwargs: kwargs["settings"])
    provider = MockLLMProvider(
        [LLMResponse.final("ignored", error_code="llm_provider_not_configured")]
    )
    monkeypatch.setattr(module, "build_llm_provider", lambda _settings: provider)

    summary = HumanTaskStartupSummary(
        owner_ref="cli_user:alice",
        total_count=1,
        todo_count=1,
        blocked_count=0,
        review_count=0,
        overdue_count=0,
        tasks=(),
    )

    rendered = await compose_human_task_startup_message(
        settings=Settings(root_dir="."),
        profile_id="default",
        summary=summary,
    )

    assert rendered is not None
    assert "Для вас есть 1 открытых задач" in rendered
    assert "afk task inbox" in rendered


async def _async_noop() -> None:
    """Return one deterministic no-op awaitable for runner tests."""

    return None
