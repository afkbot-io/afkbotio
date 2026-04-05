"""CLI commands for Task Flow containers and task items."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import typer

from afkbot.services.task_flow.cli_service import (
    add_dependency_payload,
    build_board_payload,
    create_flow_payload,
    create_task_payload,
    get_flow_payload,
    get_task_payload,
    get_task_run_payload,
    list_flows_payload,
    list_dependencies_payload,
    list_task_runs_payload,
    list_tasks_payload,
    remove_dependency_payload,
    update_task_payload,
)


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
