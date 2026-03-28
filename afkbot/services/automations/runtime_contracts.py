"""Shared runtime protocols for automation service helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from afkbot.repositories.automation_repo import AutomationRepository

TRepoValue = TypeVar("TRepoValue")


class WithAutomationRepo(Protocol):
    """Repository runner that preserves callback return types."""

    async def __call__(
        self,
        op: Callable[[AutomationRepository], Awaitable[TRepoValue]],
    ) -> TRepoValue: ...
