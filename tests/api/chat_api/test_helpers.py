"""Helper-level tests for chat API auth and targeting support."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from afkbot.api.chat_targeting import build_ws_chat_target_or_error
from afkbot.api.routes_chat import status_code_for_connect_access_error, ws_close_reason
from afkbot.services.channel_routing.runtime_target import RuntimeTarget
from afkbot.settings import Settings


def test_chat_access_error_status_mapping() -> None:
    """Chat auth helper should keep token failures at 401 and unknown errors at 400."""

    # Arrange

    # Act
    invalid_status = status_code_for_connect_access_error("connect_access_token_invalid")
    expired_status = status_code_for_connect_access_error("connect_access_token_expired")
    revoked_status = status_code_for_connect_access_error("connect_access_token_revoked")
    proof_missing_status = status_code_for_connect_access_error("connect_session_proof_missing")
    reauth_status = status_code_for_connect_access_error("connect_session_reauth_required")
    fallback_status = status_code_for_connect_access_error("connect_access_issue_failed")

    # Assert
    assert invalid_status == 401
    assert expired_status == 401
    assert revoked_status == 401
    assert proof_missing_status == 401
    assert reauth_status == 401
    assert fallback_status == 400


def test_ws_close_reason_is_short_error_code() -> None:
    """WS close reason should stay within protocol byte budget."""

    # Arrange
    auth_error = {
        "ok": False,
        "error_code": "chat_access_scope_mismatch",
        "reason": "very long diagnostic text " * 20,
    }

    # Act
    reason = ws_close_reason(auth_error)

    # Assert
    assert reason == "chat_access_scope_mismatch"
    assert len(reason.encode("utf-8")) <= 123


async def test_build_ws_chat_target_or_error_returns_target_then_error_none(
    monkeypatch: MonkeyPatch,
) -> None:
    """WS target helper should mirror auth helper tuple order on success."""

    # Arrange
    async def _fake_resolve_runtime_target(**kwargs: object) -> RuntimeTarget:  # noqa: ANN401
        _ = kwargs
        return RuntimeTarget(profile_id="default", session_id="api-s")

    monkeypatch.setattr(
        "afkbot.api.chat_targeting.resolve_runtime_target",
        _fake_resolve_runtime_target,
    )

    # Act
    target, target_error = await build_ws_chat_target_or_error(
        settings=Settings(
            root_dir=Path("."),
            db_url="sqlite+aiosqlite:///chat-targeting-success.db",
        ),
        profile_id="default",
        session_id="api-s",
        resolve_binding=False,
        require_binding_match=False,
        transport=None,
        account_id=None,
        peer_id=None,
        thread_id=None,
        user_id=None,
        default_profile_id="default",
        default_session_id="api-s",
    )

    # Assert
    assert target == RuntimeTarget(profile_id="default", session_id="api-s")
    assert target_error is None


async def test_build_ws_chat_target_or_error_returns_none_then_error(
    monkeypatch: MonkeyPatch,
) -> None:
    """WS target helper should keep error payload in the second tuple slot."""

    # Arrange
    async def _fake_resolve_runtime_target(**kwargs: object) -> RuntimeTarget:  # noqa: ANN401
        _ = kwargs
        raise ValueError("Invalid profile id: Default")

    monkeypatch.setattr(
        "afkbot.api.chat_targeting.resolve_runtime_target",
        _fake_resolve_runtime_target,
    )

    # Act
    target, target_error = await build_ws_chat_target_or_error(
        settings=Settings(
            root_dir=Path("."),
            db_url="sqlite+aiosqlite:///chat-targeting-error.db",
        ),
        profile_id="Default",
        session_id="api-s",
        resolve_binding=False,
        require_binding_match=False,
        transport=None,
        account_id=None,
        peer_id=None,
        thread_id=None,
        user_id=None,
        default_profile_id="default",
        default_session_id="api-s",
    )

    # Assert
    assert target is None
    assert target_error == {
        "ok": False,
        "error_code": "chat_request_invalid",
        "reason": "Invalid profile id: Default",
    }
