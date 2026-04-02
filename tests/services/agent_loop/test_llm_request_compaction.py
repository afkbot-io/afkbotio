"""Tests for overflow-recovery LLM request compaction."""

from __future__ import annotations

from afkbot.services.agent_loop.llm_request_compaction import LLMRequestCompactionService
from afkbot.services.llm import LLMMessage, LLMRequest, LLMResponse, MockLLMProvider


def _request(*, context: str = "ctx") -> LLMRequest:
    return LLMRequest(
        profile_id="default",
        session_id="s-1",
        context=context,
        history=[
            LLMMessage(role="user", content="first request"),
            LLMMessage(role="assistant", content="first answer"),
            LLMMessage(role="user", content="second request"),
            LLMMessage(role="assistant", content="second answer"),
            LLMMessage(role="user", content="third request"),
        ],
        available_tools=(),
    )


async def test_request_compaction_uses_llm_summary_and_keeps_recent_tail() -> None:
    """Overflow recovery should replace older history with one trusted summary block."""

    service = LLMRequestCompactionService(
        llm_provider=MockLLMProvider([LLMResponse.final("Goal: finish setup\nNext: run tests")]),
        max_summary_chars=256,
        keep_recent_turns=1,
    )

    result = await service.compact_for_overflow(request=_request(), attempt=1)

    assert result is not None
    assert result.summary_strategy == "hybrid_llm_v1"
    assert result.compacted_history is True
    assert result.request.history[0].role == "system"
    assert "Goal: finish setup" in (result.request.history[0].content or "")
    assert [item.content for item in result.request.history[1:]] == [
        "second request",
        "second answer",
        "third request",
    ]


async def test_request_compaction_falls_back_to_deterministic_summary_on_llm_failure() -> None:
    """Failed summary requests should still produce a bounded deterministic handoff."""

    service = LLMRequestCompactionService(
        llm_provider=MockLLMProvider(
            [
                LLMResponse.final(
                    "provider rejected request",
                    error_code="llm_provider_invalid_request",
                )
            ]
        ),
        max_summary_chars=120,
        keep_recent_turns=1,
    )

    result = await service.compact_for_overflow(request=_request(), attempt=1)

    assert result is not None
    assert result.summary_strategy == "deterministic_v1"
    assert result.request.history[0].role == "system"
    assert "first request" in (result.request.history[0].content or "")


async def test_request_compaction_second_attempt_can_compact_context_sections() -> None:
    """Later retry attempts should compact oversized context sections as well."""

    context = "\n\n".join(
        [
            "# Bootstrap\n" + ("A" * 2400),
            "# Skills\n" + ("B" * 2400),
            "# Runtime Safety Policy\n" + ("C" * 2400),
        ]
    )
    service = LLMRequestCompactionService(
        llm_provider=None,
        max_summary_chars=256,
        keep_recent_turns=1,
    )

    result = await service.compact_for_overflow(request=_request(context=context), attempt=2)

    assert result is not None
    assert result.compacted_context is True
    assert result.context_chars_after < result.context_chars_before
    assert "# Bootstrap" in result.request.context
    assert "# Runtime Safety Policy" in result.request.context
