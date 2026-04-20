"""Policy-focused tests for durable memory extraction."""

from __future__ import annotations

from afkbot.services.agent_loop.memory_extraction import extract_memory_candidates


def test_local_preference_is_extracted_as_candidate() -> None:
    records = extract_memory_candidates(
        user_message="В этом чате отвечай кратко.",
        assistant_message="Принял.",
        max_chars=200,
        allowed_kinds=("preference", "fact", "decision"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "preference"
    assert records[0].source_text == "В этом чате отвечай кратко"


def test_global_preference_remains_extractable_before_consolidation() -> None:
    records = extract_memory_candidates(
        user_message="Спасибо, по умолчанию отвечай по-русски и кратко.",
        assistant_message="Принял.",
        max_chars=200,
        allowed_kinds=("preference", "fact", "decision"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "preference"
    assert records[0].source_text == "по умолчанию отвечай по-русски и кратко"


def test_generic_language_preference_without_global_marker_is_candidate_only() -> None:
    records = extract_memory_candidates(
        user_message="Отвечай по-русски и кратко.",
        assistant_message="Принял.",
        max_chars=200,
        allowed_kinds=("preference", "fact", "decision"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "preference"


def test_project_wide_decision_is_extracted_as_decision_candidate() -> None:
    records = extract_memory_candidates(
        user_message="Для всего проекта решили использовать PostgreSQL по умолчанию.",
        assistant_message="Зафиксировал решение.",
        max_chars=240,
        allowed_kinds=("decision", "fact", "preference"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "decision"


def test_secret_is_rejected() -> None:
    records = extract_memory_candidates(
        user_message="API key is sk-secretsecretsecret, запомни его.",
        assistant_message="Не буду сохранять секреты.",
        max_chars=200,
        allowed_kinds=("fact", "preference"),
    )

    assert records == ()


def test_temporary_or_session_bound_content_is_rejected() -> None:
    records = extract_memory_candidates(
        user_message="На сегодня отвечай максимально формально.",
        assistant_message="Хорошо.",
        max_chars=200,
        allowed_kinds=("preference",),
    )

    assert records == ()


def test_politeness_prefix_does_not_hide_memory_signal() -> None:
    records = extract_memory_candidates(
        user_message="Спасибо, меня зовут Никита.",
        assistant_message="Рад знакомству.",
        max_chars=200,
        allowed_kinds=("fact",),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "fact"
    assert records[0].source_text == "меня зовут Никита"


def test_mixed_temporary_and_durable_candidates_preserve_durable_signal() -> None:
    records = extract_memory_candidates(
        user_message="На сегодня отвечай формально. Меня зовут Никита.",
        assistant_message="Понял.",
        max_chars=200,
        allowed_kinds=("preference", "fact"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "fact"
    assert records[0].source_text == "Меня зовут Никита"


def test_same_sentence_temporary_clause_does_not_hide_durable_fact() -> None:
    records = extract_memory_candidates(
        user_message="На сегодня отвечай формально, а меня зовут Никита.",
        assistant_message="Понял.",
        max_chars=200,
        allowed_kinds=("preference", "fact"),
    )

    assert len(records) == 1
    assert records[0].memory_kind == "fact"
    assert records[0].source_text == "меня зовут Никита"
