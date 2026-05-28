"""Tests for derived knowledge policy and redaction guards."""

from afkbot.services.knowledge.policy import KnowledgeActorContext, can_access_project_knowledge
from afkbot.services.knowledge.redaction import screen_knowledge_text


def test_knowledge_policy_blocks_user_facing_channel_by_default() -> None:
    """Project-derived knowledge should not be exposed to messaging-safe channels."""

    context = KnowledgeActorContext(
        profile_id="default",
        transport="telegram",
        channel_profile="messaging_safe",
        actor_type="human",
        actor_ref="user:1",
    )

    assert (
        can_access_project_knowledge(
            context,
            target_profile_id="default",
            allow_user_facing=False,
        )
        is False
    )


def test_knowledge_policy_blocks_unknown_transport_by_default() -> None:
    """Unknown transports should fail closed until a read surface explicitly allows them."""

    context = KnowledgeActorContext(
        profile_id="default",
        transport="custom_plugin",
        channel_profile=None,
    )

    assert (
        can_access_project_knowledge(
            context,
            target_profile_id="default",
            allow_user_facing=False,
        )
        is False
    )


def test_knowledge_policy_blocks_missing_transport_by_default() -> None:
    """Project-derived knowledge requires an explicit trusted runtime surface."""

    context = KnowledgeActorContext(profile_id="default")

    assert (
        can_access_project_knowledge(
            context,
            target_profile_id="default",
            allow_user_facing=False,
        )
        is False
    )


def test_knowledge_policy_blocks_cross_profile_access() -> None:
    """Project-derived knowledge must not cross profile boundaries."""

    context = KnowledgeActorContext(profile_id="default", transport="taskflow")

    assert (
        can_access_project_knowledge(
            context,
            target_profile_id="other",
            allow_user_facing=False,
        )
        is False
    )


def test_knowledge_redaction_rejects_secret_like_content() -> None:
    """Capture should fail closed for obvious secrets."""

    result = screen_knowledge_text("Use api_key=sk-test-secret-value for the deploy")

    assert result.allowed is False
    assert result.reason_code == "secret_detected"
