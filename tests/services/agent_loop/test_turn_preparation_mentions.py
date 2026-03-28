"""Tests for explicit @mention parsing in turn preparation."""

from __future__ import annotations

from afkbot.services.agent_loop.explicit_requests import (
    explicit_skill_references,
    explicit_skill_invocations,
    explicit_subagent_invocations,
)


def test_explicit_skill_invocations_resolve_alias_and_direct_name() -> None:
    """Skill parser should resolve explicit `@`, `$`, and slash invokes."""

    mentions = explicit_skill_invocations(
        message="Use @telegram, $imap, and /web for this task.",
        trigger_map={
            "telegram": "telegram",
            "imap": "imap",
            "web": "web-search",
            "web-search": "web-search",
        },
    )

    assert mentions == {"telegram", "imap", "web-search"}


def test_explicit_skill_references_resolve_use_phrases() -> None:
    """Natural-language explicit invokes should require an invocation phrase, not any mention."""

    mentions = explicit_skill_references(
        message="Use telegram-send for delivery and используй imap afterwards.",
        trigger_map={
            "telegram": "telegram",
            "telegram-send": "telegram",
            "imap": "imap",
        },
    )

    assert mentions == {"telegram", "imap"}


def test_explicit_skill_references_ignore_plain_descriptive_mentions() -> None:
    """Descriptive text without invoke phrasing should not become an explicit skill request."""

    mentions = explicit_skill_references(
        message="Telegram integration and imap support are listed in the platform description.",
        trigger_map={
            "telegram": "telegram",
            "imap": "imap",
        },
    )

    assert mentions == set()


def test_explicit_subagent_invocations_accept_at_and_slash_syntax() -> None:
    """Subagent parser should support the same explicit syntax as skills."""

    mentions = explicit_subagent_invocations(
        message="Delegate discovery to @researcher and /reviewer before finishing.",
        candidate_names={"researcher", "reviewer", "planner"},
    )

    assert mentions == {"researcher", "reviewer"}


def test_explicit_subagent_invocations_ignore_unknown_names() -> None:
    """Unknown explicit names should not produce false-positive subagent mentions."""

    mentions = explicit_subagent_invocations(
        message="Try @unknown and $missing first.",
        candidate_names={"researcher", "reviewer"},
    )

    assert mentions == set()
