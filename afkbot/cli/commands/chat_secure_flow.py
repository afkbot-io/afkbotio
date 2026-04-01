"""Secure chat turn runtime helpers for CLI chat command."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

import typer

from afkbot.cli.presentation.inline_select import run_inline_single_select_async
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.interactive_resume import (
    apply_approval_to_resume_call,
    apply_profile_name_to_resume_call,
    available_profile_choices,
    build_secure_resume_message,
    extract_resume_tool_call,
    is_credential_profile_question,
    is_tool_not_allowed_question,
)
from afkbot.services.agent_loop.pending_envelopes import TOOL_NOT_ALLOWED_QUESTION_KIND
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
    merge_turn_context_overrides,
)
from afkbot.services.agent_loop.turn_runtime import run_once_result, submit_secure_field
from afkbot.services.tools.base import ToolCall
from afkbot.settings import get_settings

_SECURITY_HEADER = "\033[93mAFK Agent (security)\033[0m"
_SESSION_ALLOWED_TOOL_METADATA_KEY = "session_allowed_tool_names"
_TOOL_ACCESS_DENY = "deny"
_TOOL_ACCESS_ONCE = "allow_once"
_TOOL_ACCESS_SESSION = "allow_session"

RunOnceResultFn = Callable[..., Coroutine[Any, Any, TurnResult]]
SubmitSecureFieldFn = Callable[..., Coroutine[Any, Any, tuple[bool, str]]]
ConfirmSpaceFn = Callable[..., bool | Awaitable[bool]]
ToolNotAllowedPromptFn = Callable[..., str | Awaitable[str]]
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
    confirm_space_fn: ConfirmSpaceFn | None = None,
    tool_not_allowed_prompt_fn: ToolNotAllowedPromptFn | None = None,
    session_tool_allowlist: set[str] | None = None,
) -> TurnResult:
    """Run turn and resolve secure credential requests without sending secrets to model."""

    max_interaction_steps = max(1, int(get_settings().secure_flow_max_steps))
    if tool_not_allowed_prompt_fn is None:
        tool_not_allowed_prompt_fn = _prompt_tool_not_allowed_choice
    if confirm_space_fn is None:
        confirm_space_fn = _prompt_approval_confirmation
    interaction_steps = 0
    current_message = message
    planned_tool_calls: list[ToolCall] | None = None
    runtime_one_time_allowlist: set[str] = set()
    seen_question_signatures: set[str] = set()
    while True:
        session_override: TurnContextOverrides | None = None
        merged_allowlist = set(
            str(item).strip()
            for item in (session_tool_allowlist or set())
            if str(item).strip()
        )
        if runtime_one_time_allowlist:
            merged_allowlist.update(runtime_one_time_allowlist)
        normalized_session_tools = sorted(merged_allowlist)
        if normalized_session_tools:
            session_override = TurnContextOverrides(
                runtime_metadata={
                    _SESSION_ALLOWED_TOOL_METADATA_KEY: normalized_session_tools,
                }
            )
        merged_overrides = merge_turn_context_overrides(
            runtime_overrides,
            session_override,
            turn_overrides,
        )
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
        runtime_one_time_allowlist.clear()
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
            question_signature = _build_question_signature(result.envelope)
            if question_signature in seen_question_signatures:
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="block",
                        message="Interactive flow stopped: repeated approval/question prompt.",
                        blocked_reason="interactive_flow_limit_reached",
                    ),
                )
            seen_question_signatures.add(question_signature)
            if is_tool_not_allowed_question(result.envelope):
                choice = str(
                    await _await_if_needed(
                        tool_not_allowed_prompt_fn(
                            envelope=result.envelope,
                            question_text=_tool_not_allowed_question_text(result.envelope),
                        )
                    )
                ).strip().lower()
                if choice == _TOOL_ACCESS_ONCE:
                    resume_call = extract_resume_tool_call(result.envelope)
                    if resume_call is None:
                        return TurnResult(
                            run_id=result.run_id,
                            session_id=result.session_id,
                            profile_id=result.profile_id,
                            envelope=ActionEnvelope(
                                action="block",
                                message=(
                                    "Tool execution request failed: resume tool payload is missing."
                                ),
                                blocked_reason="tool_not_allowed_resume_payload_missing",
                            ),
                        )
                    interaction_steps += 1
                    typer.echo(f"  Executing once: {resume_call.name}")
                    runtime_one_time_allowlist.add(resume_call.name)
                    current_message = f"tool_not_allowed_resume:{resume_call.name}"
                    planned_tool_calls = [resume_call]
                    continue
                if choice == _TOOL_ACCESS_SESSION:
                    if session_tool_allowlist is None:
                        session_tool_allowlist = set()
                    resume_call = extract_resume_tool_call(result.envelope)
                    if resume_call is None:
                        return TurnResult(
                            run_id=result.run_id,
                            session_id=result.session_id,
                            profile_id=result.profile_id,
                            envelope=ActionEnvelope(
                                action="block",
                                message=(
                                    "Tool execution request failed: resume tool payload is missing."
                                ),
                                blocked_reason="tool_not_allowed_resume_payload_missing",
                            ),
                        )
                    session_tool_allowlist.add(resume_call.name)
                    interaction_steps += 1
                    typer.echo(f"  Added to session allowlist: {resume_call.name}")
                    current_message = f"tool_not_allowed_resume:{resume_call.name}"
                    planned_tool_calls = [resume_call]
                    continue
                return TurnResult(
                    run_id=result.run_id,
                    session_id=result.session_id,
                    profile_id=result.profile_id,
                    envelope=ActionEnvelope(
                        action="finalize",
                        message="Operation cancelled: user denied tool execution.",
                    ),
                )
            if is_credential_profile_question(result.envelope):
                selected_profile = cast(
                    "str | None",
                    await _await_if_needed(_prompt_credential_profile_choice(result.envelope)),
                )
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
            approved = await _await_if_needed(
                confirm_space_fn(
                    question="Approve this operation now?",
                    default=False,
                    title="Safety Confirmation",
                )
            )
            if not isinstance(approved, bool):
                approved = str(approved).strip().lower() in {"1", "true", "yes", "y"}
            if not bool(approved):
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
    confirm_space_fn: ConfirmSpaceFn | None = None,
) -> RunTurnWithSecureResolution:
    """Bind trusted runtime overrides to one chat turn runner."""

    session_tool_allowlist_by_session: dict[str, set[str]] = {}

    async def _run_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        progress_sink: Callable[[ProgressEvent], None] | None,
        allow_secure_prompt: bool,
        turn_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        session_tool_allowlist = session_tool_allowlist_by_session.setdefault(session_id, set())
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
            session_tool_allowlist=session_tool_allowlist,
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



async def _prompt_tool_not_allowed_choice(*, envelope: ActionEnvelope, **kwargs: object) -> str:
    _ = kwargs
    selected = await _prompt_inline_or_text_choice(
        title="Tool access request",
        text=_tool_not_allowed_question_text(envelope),
        options=(
            (_TOOL_ACCESS_ONCE, "Run once"),
            (_TOOL_ACCESS_SESSION, "Allow for session"),
            (_TOOL_ACCESS_DENY, "Do not run"),
        ),
        default_value=_TOOL_ACCESS_DENY,
        hint_text="↑/↓ move, Enter confirm, Esc cancel",
    )
    return _TOOL_ACCESS_DENY if selected is None else selected


def _tool_not_allowed_question_text(envelope: ActionEnvelope) -> str:
    patch = envelope.spec_patch or {}
    tool_name = str(patch.get("tool_name") or "tool").strip() or "tool"
    message = str(envelope.message or "").strip()
    reason = str(patch.get("tool_not_allowed_reason") or "").strip()
    params = patch.get("tool_params")
    lines = [f"Approve running tool: {tool_name}?"]
    formatted_params = _format_tool_not_allowed_params(params)
    if formatted_params:
        lines.append("Parameters:")
        lines.append(formatted_params)
    if reason:
        lines.append(f"Reason: {reason}")
    normalized_message = message.lower()
    if message and normalized_message not in {
        "tool not allowed",
        "tool access request",
        "tool not allowed.",
        "tool access request.",
        "tool not allowed: tool access is disabled for this turn",
    }:
        lines.append(message)
    return "\n".join(lines)


def _format_tool_not_allowed_params(params: object) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    items = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
        else:
            rendered = str(value)
        items.append(f"- {key}: {rendered}")
    return "\n".join(items)


async def _prompt_approval_confirmation(
    *,
    question: str,
    default: bool,
    title: str,
    yes_label: str = "Approve",
    no_label: str = "Deny",
    hint_text: str | None = None,
    **kwargs: object,
) -> bool:
    selected = await _prompt_inline_or_text_choice(
        title=title,
        text=question,
        options=(("yes", yes_label), ("no", no_label)),
        default_value="yes" if default else "no",
        hint_text=hint_text or "↑/↓ move, Enter confirm, Esc cancel",
    )
    if selected is None:
        return default
    return selected == "yes"


async def _prompt_credential_profile_choice(envelope: ActionEnvelope) -> str | None:
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
    selected = await _prompt_inline_or_text_choice(
        title="Credential profile",
        text="Choose credential profile",
        options=tuple((profile, profile) for profile in available_profiles),
        default_value=available_profiles[0],
        hint_text="↑/↓ move, Enter confirm, Esc cancel",
    )
    selected_profile = selected.strip() if selected is not None else ""
    if selected_profile in available_profiles:
        return selected_profile
    return None


async def _prompt_inline_or_text_choice(
    *,
    title: str,
    text: str,
    options: tuple[tuple[str, str], ...],
    default_value: str,
    hint_text: str | None = None,
) -> str | None:
    selected = await run_inline_single_select_async(
        title=title,
        text=text,
        options=[(value, label) for value, label in options],
        default_value=default_value,
        hint_text=hint_text,
    )
    if selected is not None:
        resolved = _normalize_text_choice(
            answer=str(selected).strip(),
            options=options,
            default_value=default_value,
        )
        if resolved is not None:
            return resolved
    prompt = _build_choice_prompt(title=title, text=text, options=options, default=default_value)
    while True:
        answer = typer.prompt(prompt, default=default_value, show_default=False)
        resolved = _normalize_text_choice(
            answer=str(answer).strip(),
            options=options,
            default_value=default_value,
        )
        if resolved is not None:
            return resolved
        if str(answer).strip().lower() in {"q", "quit", "cancel"}:
            return None
        typer.echo("Invalid choice. Enter option number, value, or blank for default.")


def _build_question_signature(envelope: ActionEnvelope) -> str:
    patch = envelope.spec_patch or {}
    question_kind = str(patch.get("question_kind") or "").strip().lower()
    tool_name = str(patch.get("tool_name") or "").strip()
    question_id = str(envelope.question_id or "").strip()
    question = str(envelope.message or "").strip()
    message_for_signature = ""
    if question_kind == TOOL_NOT_ALLOWED_QUESTION_KIND:
        message_for_signature = str(patch.get("tool_not_allowed_reason") or "").strip()
        if not message_for_signature:
            message_for_signature = question
    params = patch.get("tool_params")
    params_text = ""
    if isinstance(params, dict):
        params_text = json.dumps(params, sort_keys=True, default=str)
    signature_parts = [question_kind, tool_name, params_text]
    if question_kind == TOOL_NOT_ALLOWED_QUESTION_KIND:
        if message_for_signature:
            signature_parts.append(message_for_signature)
    else:
        signature_parts.append(question_id or question)
    return "|".join(part for part in signature_parts if part)


def _normalize_text_choice(
    *,
    answer: str,
    options: tuple[tuple[str, str], ...],
    default_value: str,
) -> str | None:
    answer = str(answer).strip()
    if not answer:
        return default_value.strip() if default_value else None
    if answer.lower() in {"q", "quit", "cancel"}:
        return None
    if answer.isdecimal():
        index = int(answer) - 1
        if 0 <= index < len(options):
            return options[index][0]
    lowered = answer.lower()
    for value, label in options:
        if lowered in {value.lower(), label.lower()}:
            return value
    return None


def _build_choice_prompt(
    *,
    title: str,
    text: str,
    options: tuple[tuple[str, str], ...],
    default: str,
) -> str:
    values = [label for _value, label in options]
    if values:
        body = "\n".join(f"{index}. {label}" for index, label in enumerate(values, start=1))
    else:
        body = ""
    return "\n".join((title, text, body, "Enter option number, value, or blank for default:")).strip() + ": "


async def _await_if_needed(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value
