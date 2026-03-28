"""Tests for runtime-safe profile asset naming helpers."""

from __future__ import annotations

import pytest

from afkbot.services.naming import normalize_runtime_name


def test_normalize_runtime_name_transliterates_cyrillic() -> None:
    """Cyrillic labels should be converted into deterministic latin slugs."""

    assert normalize_runtime_name("Продуктолог") == "produktolog"
    assert normalize_runtime_name("проект менеджер") == "proekt-menedzher"


def test_normalize_runtime_name_cleans_symbols_and_dashes() -> None:
    """Unsafe symbols should be removed and separators normalized."""

    assert normalize_runtime_name("My__Skill  2026!!!") == "my-skill-2026"


def test_normalize_runtime_name_rejects_unrecoverable_input() -> None:
    """Inputs that cannot produce a valid slug should raise ValueError."""

    with pytest.raises(ValueError):
        normalize_runtime_name("___")
