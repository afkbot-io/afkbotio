"""Mock LLM provider for deterministic scripted tests."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.services.llm.contracts import BaseLLMProvider, LLMRequest, LLMResponse


class MockLLMProvider(BaseLLMProvider):
    """Replay scripted LLM responses and capture received requests."""

    def __init__(self, scripted_responses: Sequence[LLMResponse]) -> None:
        self._responses = list(scripted_responses)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return next scripted response or deterministic fallback."""

        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse.final("finalized: scripted provider exhausted")
