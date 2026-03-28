"""Contracts for one-shot persisted-state upgrades."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UpgradeStepReport:
    """Outcome for one idempotent upgrade step."""

    name: str
    changed: bool
    details: str


@dataclass(frozen=True, slots=True)
class UpgradeApplyReport:
    """Summary returned by `afk upgrade apply`."""

    changed: bool
    steps: tuple[UpgradeStepReport, ...]

