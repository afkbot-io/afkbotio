"""Tests for channel binding and session policy resolution."""

from __future__ import annotations

from afkbot.services.channel_routing import (
    ChannelBindingRule,
    ChannelRoutingInput,
    resolve_channel_binding,
    resolve_session_id,
)


def test_resolve_channel_binding_prefers_more_specific_rule() -> None:
    """Peer-specific binding should beat generic transport binding."""

    decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="generic",
                transport="telegram",
                profile_id="default",
                session_policy="main",
            ),
            ChannelBindingRule(
                binding_id="peer-42",
                transport="telegram",
                profile_id="sales",
                session_policy="per-thread",
                peer_id="42",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram",
            peer_id="42",
            thread_id="9001",
            default_session_id="main",
        ),
    )

    assert decision is not None
    assert decision.binding_id == "peer-42"
    assert decision.profile_id == "sales"
    assert decision.session_id == "profile:sales:chat:42:thread:9001"


def test_resolve_channel_binding_namespaces_session_id_by_profile() -> None:
    """The same routed chat selectors should get a fresh session id when the target profile changes."""

    default_decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="peer-42-default",
                transport="telegram_user",
                profile_id="default",
                session_policy="per-chat",
                peer_id="42",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram_user",
            peer_id="42",
            default_session_id="telegram_user:42",
        ),
    )
    sales_decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="peer-42-sales",
                transport="telegram_user",
                profile_id="sales",
                session_policy="per-chat",
                peer_id="42",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram_user",
            peer_id="42",
            default_session_id="telegram_user:42",
        ),
    )

    assert default_decision is not None
    assert sales_decision is not None
    assert default_decision.session_id == "profile:default:chat:42"
    assert sales_decision.session_id == "profile:sales:chat:42"
    assert default_decision.session_id != sales_decision.session_id


def test_resolve_channel_binding_namespaces_main_session_policy_by_profile() -> None:
    """Binding-resolved main sessions should also stay isolated per target profile."""

    default_decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="transport-default",
                transport="telegram_user",
                profile_id="default",
                session_policy="main",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram_user",
            default_session_id="telegram_user:42",
        ),
    )
    sales_decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="transport-sales",
                transport="telegram_user",
                profile_id="sales",
                session_policy="main",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram_user",
            default_session_id="telegram_user:42",
        ),
    )

    assert default_decision is not None
    assert sales_decision is not None
    assert default_decision.session_id == "profile:default:telegram_user:42"
    assert sales_decision.session_id == "profile:sales:telegram_user:42"
    assert default_decision.session_id != sales_decision.session_id


def test_resolve_channel_binding_ignores_disabled_rules() -> None:
    """Disabled rules should not participate in resolution."""

    decision = resolve_channel_binding(
        bindings=[
            ChannelBindingRule(
                binding_id="disabled",
                transport="telegram",
                profile_id="ops",
                enabled=False,
                peer_id="42",
            ),
        ],
        routing_input=ChannelRoutingInput(
            transport="telegram",
            peer_id="42",
            default_session_id="main",
        ),
    )

    assert decision is None


def test_resolve_session_id_supports_declared_policies() -> None:
    """Session id resolver should keep policy semantics deterministic."""

    routing_input = ChannelRoutingInput(
        transport="telegram",
        peer_id="group-1",
        thread_id="thread-7",
        user_id="user-5",
        default_session_id="main",
    )

    assert resolve_session_id(policy="main", routing_input=routing_input) == "main"
    assert resolve_session_id(policy="per-chat", routing_input=routing_input) == "chat:group-1"
    assert resolve_session_id(policy="per-thread", routing_input=routing_input) == (
        "chat:group-1:thread:thread-7"
    )
    assert resolve_session_id(policy="per-user-in-group", routing_input=routing_input) == (
        "chat:group-1:thread:thread-7:user:user-5"
    )


def test_resolve_session_id_encodes_transport_fragments_to_avoid_collisions() -> None:
    """Reserved delimiters in transport ids should be encoded before session composition."""

    routing_input = ChannelRoutingInput(
        transport="telegram",
        peer_id="room:alpha",
        thread_id="topic:7",
        user_id="user:5",
        default_session_id="main",
    )

    assert resolve_session_id(policy="per-chat", routing_input=routing_input) == "chat:room%3Aalpha"
    assert resolve_session_id(policy="per-thread", routing_input=routing_input) == (
        "chat:room%3Aalpha:thread:topic%3A7"
    )
    assert resolve_session_id(policy="per-user-in-group", routing_input=routing_input) == (
        "chat:room%3Aalpha:thread:topic%3A7:user:user%3A5"
    )


def test_resolve_session_id_falls_back_when_context_is_missing() -> None:
    """Policies should degrade safely when transport metadata is incomplete."""

    routing_input = ChannelRoutingInput(
        transport="api",
        default_session_id="api-session",
    )

    assert resolve_session_id(policy="per-chat", routing_input=routing_input) == "api-session"
    assert resolve_session_id(policy="per-thread", routing_input=routing_input) == "api-session"
    assert resolve_session_id(policy="per-user-in-group", routing_input=routing_input) == "api-session"


def test_resolve_session_id_bounds_long_generated_values() -> None:
    """Generated session ids should stay within DB-safe bounds for long selectors."""

    routing_input = ChannelRoutingInput(
        transport="telegram",
        peer_id="room-" + ("abc123" * 8),
        thread_id="topic-" + ("xyz987" * 8),
        user_id="user-" + ("555" * 10),
        default_session_id="main",
    )

    session_id = resolve_session_id(policy="per-user-in-group", routing_input=routing_input)

    assert len(session_id) <= 64
    assert ":h:" in session_id
