from __future__ import annotations

from afkbot.services.agent_loop.session_skill_affinity import SessionSkillAffinityService


def test_session_skill_affinity_reuses_known_short_followup_phrase() -> None:
    service = SessionSkillAffinityService()
    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=("imap",),
    )

    reused = service.resolve(
        profile_id="default",
        session_id="chat-1",
        raw_user_message="Да",
        explicit_skill_names=set(),
        selected_skill_names=set(),
    )

    assert reused == {"imap"}


def test_session_skill_affinity_does_not_reuse_for_unrelated_short_request() -> None:
    service = SessionSkillAffinityService()
    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=("imap",),
    )

    reused = service.resolve(
        profile_id="default",
        session_id="chat-1",
        raw_user_message="help",
        explicit_skill_names=set(),
        selected_skill_names=set(),
    )

    assert reused == set()


def test_session_skill_affinity_clears_generation_state_when_record_is_removed() -> None:
    service = SessionSkillAffinityService()
    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=("imap",),
    )

    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=(),
    )

    assert service._records == {}
    assert service._session_generations == {}


def test_session_skill_affinity_matches_russian_continue_forms() -> None:
    service = SessionSkillAffinityService()
    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=("imap",),
    )

    reused = service.resolve(
        profile_id="default",
        session_id="chat-1",
        raw_user_message="продолжай",
        explicit_skill_names=set(),
        selected_skill_names=set(),
    )

    assert reused == {"imap"}


def test_session_skill_affinity_evicts_oldest_sessions_when_capacity_is_exceeded() -> None:
    service = SessionSkillAffinityService(max_sessions=1)
    service.remember(
        profile_id="default",
        session_id="chat-1",
        selected_skill_names=("imap",),
    )
    service.remember(
        profile_id="default",
        session_id="chat-2",
        selected_skill_names=("telegram",),
    )

    assert ("default", "chat-1") not in service._records
    assert ("default", "chat-1") not in service._session_generations
    assert ("default", "chat-2") in service._records
