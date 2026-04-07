"""Human inbox startup digest helpers for chat startup notices."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, resolve_prompt_language
from afkbot.services.agent_loop.compaction_summary import CompactionSummaryRuntime
from afkbot.services.agent_loop.runtime_factory import resolve_profile_settings
from afkbot.services.llm.provider import build_llm_provider
from afkbot.services.task_flow.contracts import (
    HumanTaskInboxEventMetadata,
    HumanTaskInboxMetadata,
    HumanTaskStartupSummary,
    TaskMetadata,
)
from afkbot.settings import Settings

_DIGEST_MAX_CHARS = 900
_DIGEST_TIMEOUT_SEC = 2.5


async def compose_human_task_startup_message(
    *,
    settings: Settings,
    profile_id: str,
    summary: HumanTaskStartupSummary,
    inbox: HumanTaskInboxMetadata | None = None,
) -> str | None:
    """Return LLM-first startup digest with deterministic fallback."""

    fallback = render_human_task_startup_summary(summary, settings=settings, inbox=inbox)
    if fallback is None:
        return None
    effective_settings = resolve_profile_settings(
        settings=settings,
        profile_id=profile_id,
        ensure_layout=False,
    )
    runtime = CompactionSummaryRuntime(
        llm_provider=build_llm_provider(effective_settings),
        max_chars=_DIGEST_MAX_CHARS,
    )
    result = await runtime.summarize(
        instructions=_startup_digest_instructions(),
        source_sections=_startup_digest_sections(summary, inbox=inbox),
        fallback_text=fallback,
        preserve_if_fits=False,
    )
    rendered = result.summary_text.strip()
    return rendered or fallback


def render_human_task_startup_summary(
    summary: HumanTaskStartupSummary,
    *,
    settings: Settings | None = None,
    inbox: HumanTaskInboxMetadata | None = None,
) -> str | None:
    """Render deterministic startup notice for the current human inbox."""

    if summary.total_count <= 0:
        return None
    lang = resolve_prompt_language(settings=settings, value=None, ru=False)
    lines: list[str] = []
    if inbox is not None and inbox.recent_events:
        lines.append(
            msg(
                lang,
                en="New Task Flow activity since your last chat:",
                ru="С прошлого чата появились новые изменения в Task Flow:",
            )
        )
        for event in inbox.recent_events:
            lines.append(f"- {event.task_title}: {_render_inbox_event_preview(event=event, lang=lang)}")
        lines.append("")
    lines.append(
        msg(
            lang,
            en=f"You have {summary.total_count} open Task Flow items for you.",
            ru=f"Для вас есть {summary.total_count} открытых задач в Task Flow.",
        )
    )
    counts: list[str] = []
    if summary.todo_count:
        counts.append(msg(lang, en=f"todo: {summary.todo_count}", ru=f"todo: {summary.todo_count}"))
    if summary.blocked_count:
        counts.append(
            msg(lang, en=f"blocked: {summary.blocked_count}", ru=f"blocked: {summary.blocked_count}")
        )
    if summary.review_count:
        counts.append(msg(lang, en=f"review: {summary.review_count}", ru=f"review: {summary.review_count}"))
    if summary.overdue_count:
        counts.append(msg(lang, en=f"overdue: {summary.overdue_count}", ru=f"просрочено: {summary.overdue_count}"))
    if counts:
        lines.append(", ".join(counts))
    for task in summary.tasks:
        lines.append(f"- {_render_task_preview_line(task=task, lang=lang)}")
    remaining_count = max(summary.total_count - len(summary.tasks), 0)
    if remaining_count:
        lines.append(
            msg(
                lang,
                en=f"And {remaining_count} more task(s) are waiting in the backlog.",
                ru=f"И ещё {remaining_count} задач ждут в backlog.",
            )
        )
    lines.append(
        msg(
            lang,
            en="Use `afk task inbox` or `afk task list` if you want the full backlog.",
            ru="Используйте `afk task inbox` или `afk task list`, если нужен полный список.",
        )
    )
    return "\n".join(lines)


def _render_task_preview_line(*, task: TaskMetadata, lang: PromptLanguage) -> str:
    details: list[str] = [task.status]
    if task.priority != 50:
        details.append(msg(lang, en=f"priority {task.priority}", ru=f"приоритет {task.priority}"))
    if task.due_at is not None:
        due_text = task.due_at.astimezone().strftime("%Y-%m-%d %H:%M")
        details.append(msg(lang, en=f"due {due_text}", ru=f"срок {due_text}"))
    return f"{task.title} ({'; '.join(details)})"


def _startup_digest_instructions() -> str:
    return (
        "Write a concise assistant startup notice about the human operator's open Task Flow inbox.\n"
        "Requirements:\n"
        "- Use the same language as the source content.\n"
        "- Keep it under 6 short lines.\n"
        "- If recent inbox activity is present, mention it before the standing backlog summary.\n"
        "- Mention total counts and highlight overdue/review work when present.\n"
        "- Mention the most important preview task titles naturally.\n"
        "- Do not invent ids, deadlines, statuses, or extra tasks.\n"
        "- No markdown heading.\n"
        "- End with one short actionable sentence."
    )


def _startup_digest_sections(
    summary: HumanTaskStartupSummary,
    *,
    inbox: HumanTaskInboxMetadata | None = None,
) -> tuple[tuple[str, str], ...]:
    counts_lines = [
        f"total_count: {summary.total_count}",
        f"todo_count: {summary.todo_count}",
        f"blocked_count: {summary.blocked_count}",
        f"review_count: {summary.review_count}",
        f"overdue_count: {summary.overdue_count}",
    ]
    task_lines = [_task_source_line(task) for task in summary.tasks]
    sections: list[tuple[str, str]] = [
        ("Inbox Counts", "\n".join(counts_lines)),
        ("Preview Tasks", "\n".join(task_lines) if task_lines else "No preview tasks."),
    ]
    if inbox is not None and inbox.recent_events:
        sections.insert(
            0,
            (
                "Recent Inbox Activity",
                "\n".join(_recent_activity_source_line(event) for event in inbox.recent_events),
            ),
        )
    return tuple(sections)


def _task_source_line(task: TaskMetadata) -> str:
    parts = [f"title: {task.title}", f"status: {task.status}"]
    if task.priority != 50:
        parts.append(f"priority: {task.priority}")
    if task.due_at is not None:
        parts.append(f"due_at: {task.due_at.isoformat()}")
    if task.blocked_reason_code:
        parts.append(f"blocked_reason_code: {task.blocked_reason_code}")
    return " | ".join(parts)


def _recent_activity_source_line(event: HumanTaskInboxEventMetadata) -> str:
    parts = [f"title: {event.task_title}", f"event_type: {event.event_type}"]
    if event.to_status:
        parts.append(f"to_status: {event.to_status}")
    if event.message:
        parts.append(f"message: {event.message}")
    return " | ".join(parts)


def _render_inbox_event_preview(
    *,
    event: HumanTaskInboxEventMetadata,
    lang: PromptLanguage,
) -> str:
    if event.event_type == "comment_added":
        return event.message or msg(lang, en="new comment", ru="новый комментарий")
    if event.event_type == "execution_review_ready":
        return msg(lang, en="ready for review", ru="готово к ревью")
    if event.event_type == "execution_blocked":
        return event.message or msg(lang, en="blocked and waiting for input", ru="заблокировано и ждёт ввода")
    if event.event_type == "review_changes_requested":
        return event.message or msg(lang, en="changes requested", ru="запрошены правки")
    if event.event_type == "dependencies_satisfied":
        return msg(lang, en="dependencies satisfied, task is ready", ru="зависимости закрыты, задача готова")
    if event.event_type == "created":
        return msg(lang, en="new task assigned", ru="вам назначена новая задача")
    if event.event_type == "updated":
        if event.to_status == "review":
            return msg(lang, en="moved to review", ru="переведена в ревью")
        if event.to_status == "blocked":
            return event.message or msg(lang, en="moved to blocked", ru="переведена в blocked")
        if event.to_status == "todo":
            return msg(lang, en="moved to todo", ru="переведена в todo")
        return msg(lang, en="task was updated", ru="задача была обновлена")
    return event.message or msg(lang, en="new task activity", ru="новая активность по задаче")


__all__ = [
    "compose_human_task_startup_message",
    "render_human_task_startup_summary",
    "_DIGEST_TIMEOUT_SEC",
]
