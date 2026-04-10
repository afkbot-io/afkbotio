"""Unit tests for runtime safety policy helpers."""

from __future__ import annotations

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.safety_policy import (
    APPROVAL_REQUIRED_ERROR_CODE,
    CONFIRM_ACK_PARAM,
    CONFIRM_QID_PARAM,
    SafetyPolicy,
)


def test_extract_confirmation_ack_removes_internal_markers() -> None:
    """Confirmation markers should be consumed and normalized."""

    policy = SafetyPolicy()
    params: dict[str, object] = {
        CONFIRM_ACK_PARAM: "yes",
        CONFIRM_QID_PARAM: "approval:1",
        "cmd": "rm -rf tmp/data.txt",
    }

    confirmed, question_id = policy.extract_confirmation_ack(params)

    assert confirmed is True
    assert question_id == "approval:1"
    assert params == {"cmd": "rm -rf tmp/data.txt"}


def test_medium_policy_requires_confirmation_for_destructive_bash() -> None:
    """Medium preset should return approval-required error for destructive bash command."""

    policy = SafetyPolicy()
    profile_policy = ProfilePolicy(
        profile_id="default", policy_enabled=True, policy_preset="medium"
    )

    result = policy.approval_required_result(
        policy=profile_policy,
        tool_name="bash.exec",
        params={"cmd": "rm -rf tmp/data.txt"},
        confirmed=False,
        question_id="approval:1",
    )

    assert result is not None
    assert result.error_code == APPROVAL_REQUIRED_ERROR_CODE
    assert result.metadata == {
        "approval_mode": "medium",
        "approval_reason": (
            "Medium safety preset requires explicit yes/no confirmation for file "
            "deletion/destructive operations."
        ),
        "tool_name": "bash.exec",
        "question_id": "approval:1",
    }


def test_strict_preset_enriches_metadata_and_prompt() -> None:
    """Strict preset metadata and prompt should stay consistent for prompt shaping."""

    policy = SafetyPolicy()
    profile_policy = ProfilePolicy(
        profile_id="default", policy_enabled=True, policy_preset="strict"
    )

    metadata = policy.enrich_runtime_metadata(
        runtime_metadata={"selected_skill_requests": ["telegram"]},
        policy=profile_policy,
    )
    prompt = policy.policy_prompt_block(policy=profile_policy)

    assert metadata == {
        "selected_skill_requests": ["telegram"],
        "policy_preset": "strict",
        "safety_confirmation_mode": "confirm_all_critical_ops",
        "policy_enabled": True,
    }
    assert "Preset: strict." in prompt
    assert "Do not execute critical or mutating operations" in prompt


def test_medium_policy_requires_confirmation_for_destructive_bash_session_chars() -> None:
    """Medium preset should gate destructive resumed bash session input."""

    # Arrange
    policy = SafetyPolicy()
    profile_policy = ProfilePolicy(
        profile_id="default", policy_enabled=True, policy_preset="medium"
    )

    # Act
    result = policy.approval_required_result(
        policy=profile_policy,
        tool_name="bash.exec",
        params={"session_id": "session-1", "chars": "rm -rf tmp/data.txt\n"},
        confirmed=False,
        question_id="approval:2",
    )

    # Assert
    assert result is not None
    assert result.error_code == APPROVAL_REQUIRED_ERROR_CODE
    assert result.metadata == {
        "approval_mode": "medium",
        "approval_reason": (
            "Medium safety preset requires explicit yes/no confirmation for file "
            "deletion/destructive operations."
        ),
        "tool_name": "bash.exec",
        "question_id": "approval:2",
    }


def test_medium_policy_requires_confirmation_for_destructive_bash_batch() -> None:
    """Medium preset should gate destructive commands nested in session.job.run."""

    policy = SafetyPolicy()
    profile_policy = ProfilePolicy(
        profile_id="default", policy_enabled=True, policy_preset="medium"
    )

    result = policy.approval_required_result(
        policy=profile_policy,
        tool_name="session.job.run",
        params={
            "jobs": [
                {"kind": "bash", "cmd": "echo ok"},
                {"kind": "bash", "cmd": "truncate -s 0 tmp/data.txt"},
            ],
        },
        confirmed=False,
        question_id="approval:3",
    )

    assert result is not None
    assert result.error_code == APPROVAL_REQUIRED_ERROR_CODE
    assert result.metadata["tool_name"] == "session.job.run"


def test_simple_preset_does_not_require_confirmation_for_destructive_command() -> None:
    """Simple preset should not gate destructive commands behind approval."""

    policy = SafetyPolicy()
    profile_policy = ProfilePolicy(
        profile_id="default", policy_enabled=True, policy_preset="simple"
    )

    result = policy.approval_required_result(
        policy=profile_policy,
        tool_name="bash.exec",
        params={"cmd": "rm -rf tmp/data.txt"},
        confirmed=False,
        question_id=None,
    )

    assert result is None
