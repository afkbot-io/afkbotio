"""Pure routing helpers for channel bindings and session policies."""

from __future__ import annotations

from collections.abc import Iterable

from afkbot.services.channel_routing.contracts import (
    ChannelBindingRule,
    ChannelRoutingDecision,
    ChannelRoutingInput,
    SessionPolicy,
)
from afkbot.services.session_ids import compose_bounded_session_id, encode_session_component


def resolve_channel_binding(
    *,
    bindings: Iterable[ChannelBindingRule],
    routing_input: ChannelRoutingInput,
) -> ChannelRoutingDecision | None:
    """Resolve best matching binding for one inbound channel context."""

    matches: list[tuple[int, int, str, ChannelBindingRule]] = []
    for binding in bindings:
        if not binding.enabled:
            continue
        if binding.transport != routing_input.transport:
            continue
        if not _matches(binding=binding, routing_input=routing_input):
            continue
        matches.append((_specificity(binding), binding.priority, binding.binding_id, binding))

    if not matches:
        return None

    _, _, _, selected = max(matches, key=lambda item: (item[0], item[1], item[2]))
    effective_session_id = _scope_binding_session_id(
        profile_id=selected.profile_id,
        session_id=resolve_session_id(
            policy=selected.session_policy,
            routing_input=routing_input,
        ),
    )
    return ChannelRoutingDecision(
        binding_id=selected.binding_id,
        profile_id=selected.profile_id,
        session_policy=selected.session_policy,
        session_id=effective_session_id,
        prompt_overlay=selected.prompt_overlay,
    )


def resolve_session_id(*, policy: SessionPolicy, routing_input: ChannelRoutingInput) -> str:
    """Resolve effective session id according to one explicit session policy."""

    if policy == "main":
        return routing_input.default_session_id

    if policy == "per-chat":
        if routing_input.peer_id:
            return compose_bounded_session_id(
                "chat",
                encode_session_component(routing_input.peer_id),
            )
        return routing_input.default_session_id

    if policy == "per-thread":
        if routing_input.peer_id and routing_input.thread_id:
            return compose_bounded_session_id(
                "chat",
                encode_session_component(routing_input.peer_id),
                "thread",
                encode_session_component(routing_input.thread_id),
            )
        if routing_input.peer_id:
            return compose_bounded_session_id(
                "chat",
                encode_session_component(routing_input.peer_id),
            )
        if routing_input.thread_id:
            return compose_bounded_session_id(
                "thread",
                encode_session_component(routing_input.thread_id),
            )
        return routing_input.default_session_id

    if routing_input.peer_id and routing_input.thread_id and routing_input.user_id:
        return compose_bounded_session_id(
            "chat",
            encode_session_component(routing_input.peer_id),
            "thread",
            encode_session_component(routing_input.thread_id),
            "user",
            encode_session_component(routing_input.user_id),
        )
    if routing_input.peer_id and routing_input.user_id:
        return compose_bounded_session_id(
            "chat",
            encode_session_component(routing_input.peer_id),
            "user",
            encode_session_component(routing_input.user_id),
        )
    if routing_input.user_id:
        return compose_bounded_session_id(
            "user",
            encode_session_component(routing_input.user_id),
        )
    if routing_input.peer_id:
        return compose_bounded_session_id(
            "chat",
            encode_session_component(routing_input.peer_id),
        )
    return routing_input.default_session_id


def _matches(*, binding: ChannelBindingRule, routing_input: ChannelRoutingInput) -> bool:
    return all(
        (
            _matches_field(binding.account_id, routing_input.account_id),
            _matches_field(binding.peer_id, routing_input.peer_id),
            _matches_field(binding.thread_id, routing_input.thread_id),
            _matches_field(binding.user_id, routing_input.user_id),
        )
    )


def _matches_field(expected: str | None, actual: str | None) -> bool:
    if expected is None:
        return True
    return expected == actual


def _specificity(binding: ChannelBindingRule) -> int:
    return sum(
        1
        for item in (binding.account_id, binding.peer_id, binding.thread_id, binding.user_id)
        if item is not None
    )


def _scope_binding_session_id(*, profile_id: str, session_id: str) -> str:
    """Namespace binding-derived session ids by target profile ownership."""

    return compose_bounded_session_id(
        "profile",
        encode_session_component(profile_id),
        session_id,
    )
