"""Target resolution helpers for chat CLI."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
    merge_turn_context_overrides,
)
from afkbot.services.channel_routing import (
    ChannelBindingServiceError,
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.services.session_ids import compose_bounded_session_id, encode_session_component
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class CliChatTarget:
    """Resolved chat target plus one optional human-facing session label."""

    runtime_target: RuntimeTarget
    session_label: str | None = None
    terminal_lock_required: bool = True

    @property
    def profile_id(self) -> str:
        return self.runtime_target.profile_id

    @property
    def session_id(self) -> str:
        return self.runtime_target.session_id


@dataclass(frozen=True, slots=True)
class _CliSessionSelection:
    session_id: str
    session_label: str | None = None
    generated: bool = False


def resolve_cli_chat_target(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str | None,
    session_name: str | None,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> CliChatTarget:
    """Resolve effective profile/session for CLI chat mode."""

    if resolve_binding and not (transport or "").strip():
        raise_usage_error("--transport is required with --resolve-binding")
    session_selection = _resolve_cli_session_selection(
        profile_id=profile_id,
        session_id=session_id,
        session_name=session_name,
    )
    selectors = RoutingSelectors(
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    try:
        target = asyncio.run(
            resolve_runtime_target(
                settings=settings,
                explicit_profile_id=profile_id,
                explicit_session_id=session_selection.session_id,
                resolve_binding=resolve_binding,
                require_binding_match=require_binding_match,
                selectors=selectors,
                default_profile_id=profile_id,
                default_session_id=session_selection.session_id,
            )
        )
        session_label = (
            session_selection.session_label
            if target.session_id == session_selection.session_id
            else None
        )
        return CliChatTarget(
            runtime_target=target,
            session_label=session_label,
            terminal_lock_required=not (
                session_selection.generated and target.session_id == session_selection.session_id
            ),
        )
    except (ChannelBindingServiceError, ValueError) as exc:
        raise_usage_error(str(exc))


def _default_cli_session_id(*, profile_id: str) -> tuple[str, str]:
    normalized = profile_id.strip() or "default"
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    token = secrets.token_hex(3)
    label = f"chat-{timestamp}-{token}"
    return compose_bounded_session_id("cli", normalized, label), label


def _resolve_cli_session_selection(
    *,
    profile_id: str,
    session_id: str | None,
    session_name: str | None,
) -> _CliSessionSelection:
    explicit_session_id = (session_id or "").strip()
    explicit_session_name = (session_name or "").strip()
    if explicit_session_id and explicit_session_name:
        raise_usage_error("Pass either a positional session name or --session, not both.")
    if session_name is not None and not explicit_session_name:
        raise_usage_error("Session name cannot be empty.")
    if explicit_session_id:
        return _CliSessionSelection(session_id=explicit_session_id)
    if explicit_session_name:
        return _CliSessionSelection(
            session_id=_named_cli_session_id(
                profile_id=profile_id,
                session_name=explicit_session_name,
            ),
            session_label=explicit_session_name,
        )
    generated_session_id, generated_label = _default_cli_session_id(profile_id=profile_id)
    return _CliSessionSelection(
        session_id=generated_session_id,
        session_label=generated_label,
        generated=True,
    )


def _named_cli_session_id(*, profile_id: str, session_name: str) -> str:
    normalized = profile_id.strip() or "default"
    encoded_name = encode_session_component(session_name)
    return compose_bounded_session_id("cli", normalized, encoded_name)


def build_cli_runtime_overrides(
    *,
    target: RuntimeTarget,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Build turn context overrides from resolved routing target."""

    routing_overrides = build_routing_context_overrides(
        target=target,
        selectors=RoutingSelectors(
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        ),
    )
    runtime_metadata = dict(
        {}
        if routing_overrides is None or not routing_overrides.runtime_metadata
        else routing_overrides.runtime_metadata
    )
    if not str(runtime_metadata.get("transport") or "").strip():
        runtime_metadata["transport"] = "cli"
    cli_overrides = TurnContextOverrides(
        runtime_metadata=runtime_metadata,
        cli_approval_surface_enabled=True,
        prompt_overlay=(
            "In afk chat, some visible tools may require explicit user approval before execution. "
            "If a suitable tool is available, you may propose and call it; the runtime will request "
            "confirmation when needed. Do not claim that a visible approval-gated tool is unavailable "
            "just because it needs confirmation."
        ),
    )
    return merge_turn_context_overrides(routing_overrides, cli_overrides)
