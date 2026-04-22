"""CLI commands for Task Flow containers and task items."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from click.core import ParameterSource
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
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
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
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured owner profile filter. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured owner subagent filter inside --owner-profile.",
        ),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Task flow filter."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List tasks for one profile."""

        resolved_owner_type, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        typer.echo(
            asyncio.run(
                list_tasks_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    statuses=tuple(status),
                    owner_type=resolved_owner_type,
                    owner_ref=resolved_owner_ref,
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
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured owner profile filter. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured owner subagent filter inside --owner-profile.",
        ),
        label: list[str] = typer.Option([], "--label", help="Repeatable label filter."),
        limit_per_column: int = typer.Option(
            20,
            "--limit-per-column",
            min=1,
            help="Maximum preview tasks to show per board column.",
        ),
    ) -> None:
        """Show one Task Flow board projection."""

        resolved_owner_type, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        typer.echo(
            asyncio.run(
                build_board_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    flow_id=flow_id,
                    owner_type=resolved_owner_type,
                    owner_ref=resolved_owner_ref,
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
        owner_ref: str | None = typer.Option(
            None,
            "--owner-ref",
            help="Optional AI executor owner ref filter, for example `analyst` or `analyst:researcher`.",
        ),
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured AI executor profile filter. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured AI executor subagent filter inside --owner-profile.",
        ),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List stale claimed/running Task Flow tasks for one profile."""

        _, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=None,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        typer.echo(
            asyncio.run(
                list_stale_task_claims_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    owner_ref=resolved_owner_ref,
                    limit=limit,
                )
            )
        )

    @task_app.command("stale-sweep")
    def sweep_stale_task_claims(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        owner_ref: str | None = typer.Option(
            None,
            "--owner-ref",
            help="Optional AI executor owner ref filter, for example `analyst` or `analyst:researcher`.",
        ),
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured AI executor profile filter. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured AI executor subagent filter inside --owner-profile.",
        ),
        limit: int | None = typer.Option(
            None,
            "--limit",
            min=1,
            help="Maximum stale claims to repair in one sweep.",
        ),
    ) -> None:
        """Force one maintenance sweep for stale claimed/running Task Flow tasks."""

        _, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=None,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        typer.echo(
            asyncio.run(
                sweep_stale_task_claims_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    owner_ref=resolved_owner_ref,
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
        actor_profile: str | None = typer.Option(
            None,
            "--actor-profile",
            help="Structured reviewer profile selector. Without --actor-subagent this targets the orchestrator profile directly.",
        ),
        actor_subagent: str | None = typer.Option(
            None,
            "--actor-subagent",
            help="Structured reviewer subagent selector inside --actor-profile.",
        ),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Optional task flow filter."),
        label: list[str] = typer.Option([], "--label", help="Repeatable label filter."),
        limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum rows to return."),
    ) -> None:
        """List review queue tasks for one reviewer inbox."""

        resolved_actor_type, resolved_actor_ref = _resolve_review_actor_inputs(
            ctx=ctx,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_profile_id=actor_profile,
            actor_subagent_name=actor_subagent,
        )
        typer.echo(
            asyncio.run(
                list_review_tasks_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    actor_type=resolved_actor_type,
                    actor_ref=resolved_actor_ref,
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
        actor_profile: str | None = typer.Option(
            None,
            "--actor-profile",
            help="Structured reviewer profile selector. Without --actor-subagent this targets the orchestrator profile directly.",
        ),
        actor_subagent: str | None = typer.Option(
            None,
            "--actor-subagent",
            help="Structured reviewer subagent selector inside --actor-profile.",
        ),
    ) -> None:
        """Approve one task currently in review."""

        resolved_actor_type, resolved_actor_ref = _resolve_review_actor_inputs(
            ctx=ctx,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_profile_id=actor_profile,
            actor_subagent_name=actor_subagent,
        )
        payload = asyncio.run(
            approve_review_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                actor_type=resolved_actor_type,
                actor_ref=resolved_actor_ref,
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
        actor_profile: str | None = typer.Option(
            None,
            "--actor-profile",
            help="Structured reviewer profile selector. Without --actor-subagent this targets the orchestrator profile directly.",
        ),
        actor_subagent: str | None = typer.Option(
            None,
            "--actor-subagent",
            help="Structured reviewer subagent selector inside --actor-profile.",
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
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured owner profile reassignment. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured owner subagent reassignment inside --owner-profile.",
        ),
        reason_code: str = typer.Option(
            "review_changes_requested",
            "--reason-code",
            help="Blocked reason code to persist on the task.",
        ),
    ) -> None:
        """Request changes for one review task and move it back to blocked."""

        resolved_actor_type, resolved_actor_ref = _resolve_review_actor_inputs(
            ctx=ctx,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_profile_id=actor_profile,
            actor_subagent_name=actor_subagent,
        )
        resolved_owner_type, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        payload = asyncio.run(
            request_review_changes_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                reason_text=reason_text,
                actor_type=resolved_actor_type,
                actor_ref=resolved_actor_ref,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
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
        description: str | None = typer.Option(
            None,
            "--description",
            help="Task description or work instruction. Preferred over --prompt.",
        ),
        prompt: str | None = typer.Option(
            None,
            "--prompt",
            help="Deprecated alias for --description (kept for backward compatibility).",
        ),
        status: str = typer.Option(
            "todo",
            "--status",
            help="Initial task status. Defaults to todo for backward compatibility; use plan for manual prep.",
        ),
        flow_id: str | None = typer.Option(None, "--flow-id", help="Optional task flow id."),
        priority: int = typer.Option(50, "--priority", min=0, help="Task priority."),
        due_at: datetime | None = typer.Option(None, "--due-at", help="Optional ISO due time."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Task owner type."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Task owner ref."),
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured task owner profile. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured task owner subagent inside --owner-profile.",
        ),
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
        reviewer_profile: str | None = typer.Option(
            None,
            "--reviewer-profile",
            help="Structured reviewer profile. Without --reviewer-subagent this targets the orchestrator profile directly.",
        ),
        reviewer_subagent: str | None = typer.Option(
            None,
            "--reviewer-subagent",
            help="Structured reviewer subagent inside --reviewer-profile.",
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

        resolved_description = _resolve_task_create_description(
            description=description,
            prompt=prompt,
        )
        resolved_owner_type, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        resolved_reviewer_type, resolved_reviewer_ref = _resolve_cli_owner_inputs(
            field_prefix="reviewer",
            owner_type=reviewer_type,
            owner_ref=reviewer_ref,
            owner_profile_id=reviewer_profile,
            owner_subagent_name=reviewer_subagent,
        )
        payload = asyncio.run(
            create_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                title=title,
                description=resolved_description,
                status=status,
                created_by_type="human",
                created_by_ref=resolve_local_human_ref(get_settings()),
                flow_id=flow_id,
                priority=priority,
                due_at=due_at,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=resolved_reviewer_type,
                reviewer_ref=resolved_reviewer_ref,
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
        description: str | None = typer.Option(
            None,
            "--description",
            help="Updated task description.",
        ),
        status: str | None = typer.Option(None, "--status", help="Updated task status."),
        priority: int | None = typer.Option(None, "--priority", min=0, help="Updated priority."),
        due_at: datetime | None = typer.Option(None, "--due-at", help="Updated ISO due time."),
        owner_type: str | None = typer.Option(None, "--owner-type", help="Updated owner type."),
        owner_ref: str | None = typer.Option(None, "--owner-ref", help="Updated owner ref."),
        owner_profile: str | None = typer.Option(
            None,
            "--owner-profile",
            help="Structured updated owner profile. Without --owner-subagent this targets the orchestrator profile directly.",
        ),
        owner_subagent: str | None = typer.Option(
            None,
            "--owner-subagent",
            help="Structured updated owner subagent inside --owner-profile.",
        ),
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
        reviewer_profile: str | None = typer.Option(
            None,
            "--reviewer-profile",
            help="Structured updated reviewer profile. Without --reviewer-subagent this targets the orchestrator profile directly.",
        ),
        reviewer_subagent: str | None = typer.Option(
            None,
            "--reviewer-subagent",
            help="Structured updated reviewer subagent inside --reviewer-profile.",
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

        resolved_owner_type, resolved_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="owner",
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile,
            owner_subagent_name=owner_subagent,
        )
        resolved_reviewer_type, resolved_reviewer_ref = _resolve_cli_owner_inputs(
            field_prefix="reviewer",
            owner_type=reviewer_type,
            owner_ref=reviewer_ref,
            owner_profile_id=reviewer_profile,
            owner_subagent_name=reviewer_subagent,
        )
        payload = asyncio.run(
            update_task_payload(
                profile_id=_resolve_profile(ctx, profile),
                task_id=task_id,
                title=title,
                description=description,
                status=status,
                priority=priority,
                due_at=due_at,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=resolved_reviewer_type,
                reviewer_ref=resolved_reviewer_ref,
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
                actor_type="human",
                actor_ref=resolve_local_human_ref(get_settings()),
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
                actor_type="human",
                actor_ref=resolve_local_human_ref(get_settings()),
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
        default_owner_profile: str | None = typer.Option(
            None,
            "--default-owner-profile",
            help="Structured default owner profile. Without --default-owner-subagent this targets the orchestrator profile directly.",
        ),
        default_owner_subagent: str | None = typer.Option(
            None,
            "--default-owner-subagent",
            help="Structured default owner subagent inside --default-owner-profile.",
        ),
        label: list[str] = typer.Option([], "--label", help="Repeatable task flow label."),
    ) -> None:
        """Create one task flow container."""

        resolved_default_owner_type, resolved_default_owner_ref = _resolve_cli_owner_inputs(
            field_prefix="default_owner",
            owner_type=default_owner_type,
            owner_ref=default_owner_ref,
            owner_profile_id=default_owner_profile,
            owner_subagent_name=default_owner_subagent,
        )
        payload = asyncio.run(
            create_flow_payload(
                profile_id=_resolve_profile(ctx, profile),
                title=title,
                description=description,
                created_by_type="human",
                created_by_ref=resolve_local_human_ref(get_settings()),
                default_owner_type=resolved_default_owner_type,
                default_owner_ref=resolved_default_owner_ref,
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


def _resolve_cli_owner_inputs(
    *,
    field_prefix: str,
    owner_type: str | None,
    owner_ref: str | None,
    owner_profile_id: str | None,
    owner_subagent_name: str | None,
) -> tuple[str | None, str | None]:
    """Resolve raw or structured CLI owner inputs into one normalized type/ref pair."""

    try:
        return resolve_task_owner_inputs(
            field_prefix=field_prefix,
            owner_type=owner_type,
            owner_ref=owner_ref,
            owner_profile_id=owner_profile_id,
            owner_subagent_name=owner_subagent_name,
        )
    except TaskOwnerInputError as exc:
        raise typer.BadParameter(
            exc.reason,
            param_hint=f"--{field_prefix.replace('_', '-')}-profile",
        ) from None


def _resolve_review_actor_inputs(
    *,
    ctx: typer.Context,
    actor_type: str,
    actor_ref: str | None,
    actor_profile_id: str | None,
    actor_subagent_name: str | None,
) -> tuple[str, str]:
    """Resolve reviewer actor selector for CLI review surfaces."""

    structured_actor_present = bool(
        (actor_profile_id is not None and actor_profile_id.strip())
        or (actor_subagent_name is not None and actor_subagent_name.strip())
    )
    effective_actor_type: str | None = actor_type
    if (
        structured_actor_present
        and actor_type == "human"
        and actor_ref is None
        and not _option_was_explicit(ctx, "actor_type")
    ):
        effective_actor_type = None
    resolved_type, resolved_ref = _resolve_cli_owner_inputs(
        field_prefix="actor",
        owner_type=effective_actor_type,
        owner_ref=actor_ref,
        owner_profile_id=actor_profile_id,
        owner_subagent_name=actor_subagent_name,
    )
    if resolved_type is None and resolved_ref is None:
        return "human", resolve_local_human_ref(get_settings())
    if resolved_type == "human" and resolved_ref is None:
        return "human", resolve_local_human_ref(get_settings())
    if resolved_type is None or resolved_ref is None:
        raise typer.BadParameter("--actor-ref is required unless --actor-type=human")
    return resolved_type, resolved_ref


def _option_was_explicit(ctx: typer.Context, param_name: str) -> bool:
    """Return whether one Typer/Click option came from user input instead of the default."""

    getter = getattr(ctx, "get_parameter_source", None)
    if getter is None:
        return False
    return getter(param_name) is not ParameterSource.DEFAULT


def _resolve_task_create_description(*, description: str | None, prompt: str | None) -> str:
    """Resolve required task description across current and legacy flags.

    During the transition period, `--prompt` is accepted as a backward-compatible alias.
    When both are provided, `--description` wins deterministically.
    """

    if description is not None and description.strip():
        return description
    if prompt is not None and prompt.strip():
        return prompt
    raise typer.BadParameter(
        "task description is required; provide --description (preferred) or legacy --prompt",
        param_hint="--description",
    )
