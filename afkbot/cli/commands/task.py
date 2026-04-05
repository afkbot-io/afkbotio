"""CLI commands for Task Flow containers and task items."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import typer

from afkbot.services.task_flow.cli_service import (
    add_task_comment_payload,
    add_dependency_payload,
    approve_review_task_payload,
    build_board_payload,
    build_human_inbox_payload,
    create_flow_payload,
    create_task_payload,
    get_flow_payload,
    get_task_payload,
    get_task_run_payload,
    list_task_comments_payload,
    list_flows_payload,
    list_dependencies_payload,
    list_task_events_payload,
    list_review_tasks_payload,
    list_task_runs_payload,
    list_tasks_payload,
    remove_dependency_payload,
    request_review_changes_payload,
    list_stale_task_claims_payload,
    sweep_stale_task_claims_payload,
    update_task_payload,
)
from afkbot.services.task_flow.human_ref import resolve_local_human_ref
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register task CLI group."""

    task_app = typer.Typer(
        help="Manage Task Flow containers and tasks.",
        no_args_is_help=True,
    )
    app.add_typer(task_app, name="task")

    @task_app.callback()
    def task_group(
        ctx: typer.Context,
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Default target profile id for task subcommands.",
        ),
    ) -> None:
        """Capture optional group-level task defaults."""

        ctx.ensure_object(dict)
        if profile is not None:
            ctx.obj["profile"] = profile

    @task_app.command("list")
    def list_tasks(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        status: list[str] = typer.Option([], "--status", help="Optional task status filter."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Owner type filter."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Owner ref filter."),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Task flow filter."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List tasks for one profile."""

        typer.echo(
            asyncio.run(
                list_tasks_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    statuses=tuple(status),
                    owner_type=owner_type,
                    owner_ref=owner_ref,
                    flow_id=flow_id,
                    limit=limit,
                )
            )
        )

    @task_app.command("board")
    def show_board(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Optional task flow filter."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Owner type filter."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Owner ref filter."),
        label: list[str] = typer.Option([], "--label", help="Repeatable label filter."),
        limit_per_column: int = typer.Option(
            20,
            "--limit-per-column",
            min=1,
            help="Maximum preview tasks to show per board column.",
        ),
    ) -> None:
        """Show one Task Flow board projection."""

        typer.echo(
            asyncio.run(
                build_board_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    flow_id=flow_id,
                    owner_type=owner_type,
                    owner_ref=owner_ref,
                    labels=tuple(label),
                    limit_per_column=limit_per_column,
                )
            )
        )

    @task_app.command("inbox")
    def inbox_command(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        owner_ref: str | None = typer.Option(
            None,
            "--owner-ref",
            help="Human owner ref. Defaults to the local human ref for CLI use.",
        ),
        task_limit: int = typer.Option(5, "--task-limit", min=1, help="Maximum preview tasks."),
        event_limit: int = typer.Option(
            5,
            "--event-limit",
            min=1,
            help="Maximum recent inbox events to include.",
        ),
        channel: str | None = typer.Option(
            None,
            "--channel",
            help="Optional dedupe channel scope for notification cursors.",
        ),
        mark_seen: bool = typer.Option(
            False,
            "--mark-seen/--no-mark-seen",
            help="Advance the dedupe cursor for the selected channel.",
        ),
    ) -> None:
        """Show notification-ready human inbox summary."""

        resolved_owner_ref = owner_ref or resolve_local_human_ref(get_settings())
        if mark_seen and owner_ref is not None:
            local_owner_ref = resolve_local_human_ref(get_settings())
            if resolved_owner_ref != local_owner_ref:
                raise typer.BadParameter(
                    "mark_seen can only be used for the local human inbox",
                    param_hint="--owner-ref",
                )
        typer.echo(
            asyncio.run(
                build_human_inbox_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    owner_ref=resolved_owner_ref,
                    task_limit=task_limit,
                    event_limit=event_limit,
                    channel=channel,
                    mark_seen=mark_seen,
                )
            )
        )

    @task_app.command("stale-list")
    def list_stale_task_claims(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List stale claimed/running Task Flow tasks for one profile."""

        typer.echo(
            asyncio.run(
                list_stale_task_claims_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    limit=limit,
                )
            )
        )

    @task_app.command("stale-sweep")
    def sweep_stale_task_claims(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        limit: int | None = typer.Option(
            None,
            "--limit",
            min=1,
            help="Maximum stale claims to repair in one sweep.",
        ),
    ) -> None:
        """Force one maintenance sweep for stale claimed/running Task Flow tasks."""

        typer.echo(
            asyncio.run(
                sweep_stale_task_claims_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    limit=limit,
                )
            )
        )

    @task_app.command("review-list")
    def list_review_tasks(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        actor_type: str = typer.Option("human", "--actor-type", help="Reviewer actor type."),
        actor_ref: str | None = typer.Option(
            None,
            "--actor-ref",
            help="Reviewer actor ref. Defaults to the local human ref for CLI use.",
        ),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Optional task flow filter."),
        label: list[str] = typer.Option([], "--label", help="Repeatable label filter."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List review queue tasks for one reviewer inbox."""

        typer.echo(
            asyncio.run(
                list_review_tasks_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    actor_type=actor_type,
                    actor_ref=_resolve_review_actor_ref(actor_type=actor_type, actor_ref=actor_ref),
                    flow_id=flow_id,
                    labels=tuple(label),
                    limit=limit,
                )
            )
        )

    @task_app.command("review-approve")
    def approve_review_task(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        actor_type: str = typer.Option("human", "--actor-type", help="Reviewer actor type."),
        actor_ref: str | None = typer.Option(
            None,
            "--actor-ref",
            help="Reviewer actor ref. Defaults to the local human ref for CLI use.",
        ),
    ) -> None:
        """Approve one task currently in review."""

        payload = asyncio.run(
            approve_review_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                actor_type=actor_type,
                actor_ref=_resolve_review_actor_ref(actor_type=actor_type, actor_ref=actor_ref),
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("review-request-changes")
    def request_review_changes(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        reason_text: str = typer.Option(..., "--reason-text", help="Required review feedback."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        actor_type: str = typer.Option("human", "--actor-type", help="Reviewer actor type."),
        actor_ref: str | None = typer.Option(
            None,
            "--actor-ref",
            help="Reviewer actor ref. Defaults to the local human ref for CLI use.",
        ),
        owner_type: str | None = typer.Option(
            None,
            "--owner-type",
            help="Optional owner reassignment while returning the task for changes.",
        ),
        owner_ref: str | None = typer.Option(
            None,
            "--owner-ref",
            help="Optional owner ref reassignment while returning the task for changes.",
        ),
        reason_code: str = typer.Option(
            "review_changes_requested",
            "--reason-code",
            help="Blocked reason code to persist on the task.",
        ),
    ) -> None:
        """Request changes for one review task and move it back to blocked."""

        payload = asyncio.run(
            request_review_changes_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                reason_text=reason_text,
                actor_type=actor_type,
                actor_ref=_resolve_review_actor_ref(actor_type=actor_type, actor_ref=actor_ref),
                owner_type=owner_type,
                owner_ref=owner_ref,
                reason_code=reason_code,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("show")
    @task_app.command("get")
    def show_task(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Show one task metadata record."""

        typer.echo(
            asyncio.run(
                get_task_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_id=task_id,
                )
            )
        )

    @task_app.command("create")
    def create_task(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        title: str = typer.Option(..., "--title", help="Task title."),
        prompt: str = typer.Option(..., "--prompt", help="Task prompt or work instruction."),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Optional task flow id."),
        priority: int = typer.Option(50, "--priority", min=0, help="Task priority."),
        due_at: datetime | None = typer.Option(None, "--due-at", help="Optional ISO due time."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Task owner type."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Task owner ref."),
        reviewer_type: str | None = typer.Option(
            None,
            "--reviewer-type",
            help="Optional reviewer owner type.",
        ),
        reviewer_ref: str | None = typer.Option(
            None,
            "--reviewer-ref",
            help="Optional reviewer owner ref.",
        ),
        label: list[str] = typer.Option([], "--label", help="Repeatable task label."),
        requires_review: bool = typer.Option(
            False,
            "--requires-review/--no-requires-review",
            help="Mark task as needing review after execution.",
        ),
        depends_on: list[str] = typer.Option(
            [],
            "--depends-on",
            help="Repeatable dependency task id.",
        ),
    ) -> None:
        """Create one task under the selected profile."""

        payload = asyncio.run(
            create_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                title=title,
                prompt=prompt,
                created_by_type="human",
                created_by_ref="cli",
                flow_id=flow_id,
                priority=priority,
                due_at=due_at,
                owner_type=owner_type,
                owner_ref=owner_ref,
                reviewer_type=reviewer_type,
                reviewer_ref=reviewer_ref,
                labels=tuple(label),
                requires_review=requires_review,
                depends_on_task_ids=tuple(depends_on),
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("update")
    def update_task(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        title: str | None = typer.Option(None, "--title", help="Updated task title."),
        prompt: str | None = typer.Option(None, "--prompt", help="Updated task prompt."),
        status: str | None = typer.Option(None, "--status", help="Updated task status."),
        priority: int | None = typer.Option(None, "--priority", min=0, help="Updated priority."),
        due_at: datetime | None = typer.Option(None, "--due-at", help="Updated ISO due time."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Updated owner type."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Updated owner ref."),
        reviewer_type: str | None = typer.Option(
            None,
            "--reviewer-type",
            help="Updated reviewer type.",
        ),
        reviewer_ref: str | None = typer.Option(
            None,
            "--reviewer-ref",
            help="Updated reviewer ref.",
        ),
        label: list[str] | None = typer.Option(
            None,
            "--label",
            help="Repeatable replacement label list.",
        ),
        requires_review: bool | None = typer.Option(
            None,
            "--requires-review",
            help="Override review requirement.",
        ),
        blocked_reason_code: str | None = typer.Option(
            None,
            "--blocked-reason-code",
            help="Optional blocked reason code.",
        ),
        blocked_reason_text: str | None = typer.Option(
            None,
            "--blocked-reason-text",
            help="Optional blocked reason text.",
        ),
    ) -> None:
        """Update one task fields."""

        payload = asyncio.run(
            update_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                title=title,
                prompt=prompt,
                status=status,
                priority=priority,
                due_at=due_at,
                owner_type=owner_type,
                owner_ref=owner_ref,
                reviewer_type=reviewer_type,
                reviewer_ref=reviewer_ref,
                requires_review=requires_review,
                labels=(tuple(label) if label is not None else None),
                blocked_reason_code=blocked_reason_code,
                blocked_reason_text=blocked_reason_text,
                actor_type="human",
                actor_ref=resolve_local_human_ref(get_settings()),
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("dependency-list")
    def list_dependencies(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """List dependency edges for one task."""

        typer.echo(
            asyncio.run(
                list_dependencies_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_id=task_id,
                )
            )
        )

    @task_app.command("dependency-add")
    def add_dependency(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        depends_on_task_id: str = typer.Option(..., "--depends-on", help="Prerequisite task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        satisfied_on_status: str = typer.Option(
            "completed",
            "--satisfied-on-status",
            help="Prerequisite status required to satisfy the edge.",
        ),
    ) -> None:
        """Add one dependency edge for the selected task."""

        payload = asyncio.run(
            add_dependency_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                depends_on_task_id=depends_on_task_id,
                satisfied_on_status=satisfied_on_status,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("dependency-remove")
    def remove_dependency(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        depends_on_task_id: str = typer.Option(..., "--depends-on", help="Prerequisite task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Remove one dependency edge from the selected task."""

        payload = asyncio.run(
            remove_dependency_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                depends_on_task_id=depends_on_task_id,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("run-list")
    def list_task_runs(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        task_id: str | None = typer.Option(None, "--task-id", help="Optional task id filter."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List execution attempts for one profile or task."""

        typer.echo(
            asyncio.run(
                list_task_runs_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_id=task_id,
                    limit=limit,
                )
            )
        )

    @task_app.command("event-list")
    def list_task_events(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List append-only task events for one task."""

        typer.echo(
            asyncio.run(
                list_task_events_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_id=task_id,
                    limit=limit,
                )
            )
        )

    @task_app.command("comment-list")
    def list_task_comments(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List append-only task comments for one task."""

        typer.echo(
            asyncio.run(
                list_task_comments_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_id=task_id,
                    limit=limit,
                )
            )
        )

    @task_app.command("comment-add")
    def add_task_comment(
        ctx: typer.Context,
        task_id: str = typer.Argument(..., help="Task id."),
        message: str = typer.Option(..., "--message", help="Comment body."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        comment_type: str = typer.Option("note", "--comment-type", help="Comment type."),
        task_run_id: int | None = typer.Option(
            None,
            "--task-run-id",
            help="Optional task run id when the comment refers to one execution attempt.",
        ),
    ) -> None:
        """Append one human comment to the selected task."""

        payload = asyncio.run(
            add_task_comment_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                message=message,
                actor_type="human",
                actor_ref=resolve_local_human_ref(get_settings()),
                comment_type=comment_type,
                task_run_id=task_run_id,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @task_app.command("run-get")
    def get_task_run(
        ctx: typer.Context,
        task_run_id: int = typer.Argument(..., help="Task run id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Show one execution attempt."""

        typer.echo(
            asyncio.run(
                get_task_run_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    task_run_id=task_run_id,
                )
            )
        )

    @task_app.command("flow-list")
    def list_flows(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """List task flow containers for one profile."""

        typer.echo(asyncio.run(list_flows_payload(profile_id=_resolve_profile(ctx, profile))))

    @task_app.command("flow-show")
    @task_app.command("flow-get")
    def show_flow(
        ctx: typer.Context,
        flow_id: str = typer.Argument(..., help="Task flow id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Show one task flow container."""

        typer.echo(
            asyncio.run(
                get_flow_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    flow_id=flow_id,
                )
            )
        )

    @task_app.command("flow-create")
    def create_flow(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        title: str = typer.Option(..., "--title", help="Task flow title."),
        description: str | None = typer.Option(
            None,
            "--description",
            help="Optional task flow description.",
        ),
        default_owner_type: str | None = typer.Option(
            None,
            "--default-owner-type",
            help="Default owner type for tasks in this flow.",
        ),
        default_owner_ref: str | None = typer.Option(
            None,
            "--default-owner-ref",
            help="Default owner ref for tasks in this flow.",
        ),
        label: list[str] = typer.Option([], "--label", help="Repeatable task flow label."),
    ) -> None:
        """Create one task flow container."""

        payload = asyncio.run(
            create_flow_payload(
                profile_id=_resolve_profile(ctx, profile),
                title=title,
                description=description,
                created_by_type="human",
                created_by_ref="cli",
                default_owner_type=default_owner_type,
                default_owner_ref=default_owner_ref,
                labels=tuple(label),
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)


def _resolve_profile(ctx: typer.Context, profile: str | None) -> str:
    """Resolve subcommand profile from explicit flag, group option, or default."""

    if profile is not None and profile.strip():
        return profile
    if isinstance(ctx.obj, dict):
        group_profile = ctx.obj.get("profile")
        if isinstance(group_profile, str) and group_profile.strip():
            return group_profile
    return "default"


def _exit_on_error_payload(payload: str) -> None:
    data = json.loads(payload)
    if data.get("ok") is False:
        raise typer.Exit(code=1)


def _resolve_review_actor_ref(*, actor_type: str, actor_ref: str | None) -> str:
    """Resolve reviewer actor ref for CLI review surfaces."""

    if actor_ref is not None and actor_ref.strip():
        return actor_ref
    if actor_type != "human":
        raise typer.BadParameter("--actor-ref is required unless --actor-type=human")
    return resolve_local_human_ref(get_settings())
