"""Secure chat turn runtime helpers for CLI chat command."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import typer

from afkbot.cli.presentation import confirm_space
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.interactive_resume import (
    apply_approval_to_resume_call,
    apply_profile_name_to_resume_call,
    available_profile_choices,
    build_secure_resume_message,
    extract_resume_tool_call,
    is_credential_profile_question,
)
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
    merge_turn_context_overrides,
)
from afkbot.services.agent_loop.turn_runtime import run_once_result, submit_secure_field
from afkbot.services.tools.base import ToolCall
from afkbot.settings import get_settings

_SECURITY_HEADER = "\033[93mAFK Agent (security)\033[0m"

RunOnceResultFn = Callable[..., Coroutine[Any, Any, TurnResult]]
SubmitSecureFieldFn = Callable[..., Coroutine[Any, Any, tuple[bool, str]]]
ConfirmSpaceFn = Callable[..., bool]
RunTurnWithSecureResolution = Callable[..., Coroutine[Any, Any, TurnResult]]


async def run_turn_with_secure_resolution(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    progress_sink: Callable[[ProgressEvent], None] | None,
    allow_secure_prompt: bool,
    runtime_overrides: TurnContextOverrides | None = None,
    turn_overrides: TurnContextOverrides | None = None,
    run_once_result_fn: RunOnceResultFn = run_once_result,
    submit_secure_field_fn: SubmitSecureFieldFn = submit_secure_field,
    confirm_space_fn: ConfirmSpaceFn = confirm_space,
) -> TurnResult:
    """Run turn and resolve secure credential requests without sending secrets to model."""

    max_interaction_steps = max(1, int(get_settings().secure_flow_max_steps))
    interaction_steps = 0
    current_message = message
    planned_tool_calls: list[ToolCall] | None = None
    while True:
        merged_overrides = merge_turn_context_overrides(runtime_overrides, turn_overrides)
        if merged_overrides is None:
            result = await run_once_result_fn(
                message=current_message,
                profile_id=profile_id,
                session_id=session_id,
                planned_tool_calls=planned_tool_calls,
                progress_sink=progress_sink,
            )
        else:
            result = await run_once_result_fn(
                message=current_message,
                profile_id=profile_id,
                session_id=session_id,
                planned_tool_calls=planned_tool_calls,
                progress_sink=progress_sink,
                context_overrides=merged_overrides,
            )
        if result.envelope.action == "request_secure_field" and allow_secure_prompt:
            if interaction_steps >= max_interaction_steps:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="block",
                        message="Interactive flow stopped: too many prompts in one turn.",
                        blocked_reason="interactive_flow_limit_reached",
                    ),
                )
            _render_security_prompt(result.envelope)
            secure_field = (result.envelope.secure_field or "credential").strip() or "credential"
            secret_value = typer.prompt(
                f"Secure value for {secure_field}",
                hide_input=True,
            )
            ok, code = await submit_secure_field_fn(
                profile_id=profile_id,
                envelope=result.envelope,
                secret_value=secret_value,
                session_id=result.session_id,
            )
            if not ok:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="block",
                        message=f"Failed to store secure value: {code}",
                        blocked_reason=code,
                    ),
                )
            interaction_steps += 1
            resume_call = extract_resume_tool_call(result.envelope)
            if resume_call is None:
                current_message = build_secure_resume_message(result.envelope)
                planned_tool_calls = None
                continue
            typer.echo(f"  Resuming tool: {resume_call.name}")
            current_message = f"secure_resume:{resume_call.name}"
            planned_tool_calls = [resume_call]
            continue
        if result.envelope.action == "ask_question" and allow_secure_prompt:
            if interaction_steps >= max_interaction_steps:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="block",
                        message="Interactive flow stopped: too many prompts in one turn.",
                        blocked_reason="interactive_flow_limit_reached",
                    ),
                )
            if is_credential_profile_question(result.envelope):
                selected_profile = _prompt_credential_profile_choice(result.envelope)
                if not selected_profile:
                    return TurnResult(
                        run_id=result.run_id,
                        session_id=result.session_id,
                        profile_id=result.profile_id,
                        envelope=ActionEnvelope(
                            action="block",
                            message=(
                                "Credential profile selection failed: "
                                "available credential profiles are missing."
                            ),
                            blocked_reason="credential_profile_choices_missing",
                        ),
                    )
                resume_call = extract_resume_tool_call(result.envelope)
                if resume_call is None:
                    return TurnResult(
                        run_id=result.run_id,
                        session_id=result.session_id,
                        profile_id=result.profile_id,
                        envelope=ActionEnvelope(
                            action="block",
                            message="Credential profile selection failed: resume tool payload is missing.",
                            blocked_reason="credential_profile_resume_payload_missing",
                        ),
                    )
                resumed_call = apply_profile_name_to_resume_call(
                    tool_call=resume_call,
                    profile_name=selected_profile,
                )
                interaction_steps += 1
                typer.echo(
                    f"  Selected credential profile: {selected_profile}. "
                    f"Resuming tool: {resumed_call.name}",
                )
                current_message = f"profile_resume:{resumed_call.name}"
                planned_tool_calls = [resumed_call]
                continue
            _render_approval_prompt(result.envelope)
            approved = confirm_space_fn(
                question="Approve this operation now?",
                default=False,
                title="Safety Confirmation",
            )
            if not approved:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="finalize",
                        message="Operation cancelled: confirmation denied by user.",
                    ),
                )
            resume_call = extract_resume_tool_call(result.envelope)
            if resume_call is None:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="block",
                        message="Safety confirmation failed: resume tool payload is missing.",
                        blocked_reason="approval_resume_payload_missing",
                    ),
                )
            resumed_call = apply_approval_to_resume_call(
                tool_call=resume_call,
                question_id=result.envelope.question_id,
            )
            interaction_steps += 1
            typer.echo(f"  Approval confirmed. Resuming tool: {resumed_call.name}")
            current_message = f"approval_resume:{resumed_call.name}"
            planned_tool_calls = [resumed_call]
            continue
        return result


def build_run_turn_with_overrides(
    runtime_overrides: TurnContextOverrides | None,
    *,
    run_once_result_fn: RunOnceResultFn = run_once_result,
    submit_secure_field_fn: SubmitSecureFieldFn = submit_secure_field,
    confirm_space_fn: ConfirmSpaceFn = confirm_space,
) -> RunTurnWithSecureResolution:
    """Bind trusted runtime overrides to one chat turn runner."""

    async def _run_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink: Callable[[ProgressEvent], None] | None,
        allow_secure_prompt: bool,
        turn_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        return await run_turn_with_secure_resolution(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            progress_sink=progress_sink,
            allow_secure_prompt=allow_secure_prompt,
            runtime_overrides=runtime_overrides,
            turn_overrides=turn_overrides,
            run_once_result_fn=run_once_result_fn,
            submit_secure_field_fn=submit_secure_field_fn,
            confirm_space_fn=confirm_space_fn,
        )

    return _run_turn


def _render_security_prompt(envelope: ActionEnvelope) -> None:
    patch = envelope.spec_patch or {}
    integration = str(patch.get("integration_name") or "integration").strip() or "integration"
    profile = str(patch.get("credential_profile_key") or "default").strip() or "default"
    field_name = (
        str(patch.get("credential_name") or envelope.secure_field or "credential").strip()
        or "credential"
    )
    typer.echo("")
    typer.echo(_SECURITY_HEADER)
    typer.echo("  Secure input required")
    typer.echo(f"  Integration: {integration}")
    typer.echo(f"  Credential profile: {profile}")
    typer.echo(f"  Field: {field_name}")
    typer.echo("  This value is stored securely and never sent to the model.")


def _render_approval_prompt(envelope: ActionEnvelope) -> None:
    patch = envelope.spec_patch or {}
    mode = str(patch.get("approval_mode") or "strict").strip() or "strict"
    tool_name = str(patch.get("tool_name") or "tool").strip() or "tool"
    typer.echo("")
    typer.echo(_SECURITY_HEADER)
    typer.echo(f"  Safety mode: {mode}")
    typer.echo(f"  Pending tool: {tool_name}")
    typer.echo(f"  {envelope.message}")


def _prompt_credential_profile_choice(envelope: ActionEnvelope) -> str | None:
    patch = envelope.spec_patch or {}
    available_profiles = available_profile_choices(envelope)
    if not available_profiles:
        return None
    integration = str(patch.get("integration_name") or "integration").strip() or "integration"
    credential_name = (
        str(patch.get("credential_name") or envelope.secure_field or "credential").strip()
        or "credential"
    )
    typer.echo("")
    typer.echo(_SECURITY_HEADER)
    typer.echo(f"  Integration: {integration}")
    typer.echo(f"  Credential field: {credential_name}")
    typer.echo(f"  Available credential profiles: {', '.join(available_profiles)}")
    while True:
        selected = typer.prompt("Credential profile", default=available_profiles[0])
        selected = str(selected).strip()
        if selected in available_profiles:
            return selected
        typer.echo(f"  Invalid credential profile. Choose one of: {', '.join(available_profiles)}")
