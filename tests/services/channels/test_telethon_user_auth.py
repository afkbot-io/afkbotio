"""Tests for Telethon auth/probe/logout helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta
import importlib

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.channels.telethon_user.auth import (
    authorize_telethon_endpoint,
    logout_telethon_endpoint,
    probe_telethon_endpoint,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
async def _reset_cached_services() -> None:
    await reset_channel_endpoint_services_async()
    await reset_profile_services_async()
    yield
    await reset_channel_endpoint_services_async()
    await reset_profile_services_async()


class _FakeTelethonClient:
    def __init__(self) -> None:
        self.connected = False
        self.logged_out = False
        self.requested_phone: str | None = None
        self.sign_in_calls: list[dict[str, str]] = []
        self.qr_wait_called = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> object:
        return SimpleNamespace(
            id=1001,
            username="afkme",
            phone="+79990000000",
            first_name="Afk",
            last_name="Me",
        )

    async def send_code_request(self, phone: str) -> object:
        self.requested_phone = phone
        app_type = type("SentCodeTypeApp", (), {})()
        next_type = type("CodeTypeSms", (), {})()
        return SimpleNamespace(type=app_type, next_type=next_type, timeout=60)

    async def qr_login(self, ignored_ids: list[int] | None = None) -> object:
        _ = ignored_ids
        client = self

        class _QrLogin:
            url = "tg://login?token=qr-token"
            expires = datetime.now(tz=UTC) + timedelta(seconds=60)

            async def wait(self, timeout: float = None) -> None:
                _ = timeout
                client.qr_wait_called = True

        return _QrLogin()

    async def sign_in(self, **kwargs: str) -> object:
        self.sign_in_calls.append(kwargs)
        return object()

    async def log_out(self) -> None:
        self.logged_out = True


async def _seed_profile(settings: Settings, *, allow_mtproto: bool) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    await engine.dispose()
    profiles = ProfileService(settings)
    try:
        await profiles.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("*",) if allow_mtproto else ("api.telegram.org",),
        )
    finally:
        await profiles.shutdown()


def _endpoint() -> TelethonUserEndpointConfig:
    return TelethonUserEndpointConfig(
        endpoint_id="personal-user",
        profile_id="default",
        credential_profile_key="tg-user",
        account_id="personal-user",
    )


async def test_probe_telethon_endpoint_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live probe must not create or mutate Telethon runtime state."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_probe.db'}",
    )
    await _seed_profile(settings, allow_mtproto=True)
    fake_client = _FakeTelethonClient()

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string="session",
            phone="+79990000000",
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        lambda **kwargs: fake_client,
    )

    identity = await probe_telethon_endpoint(settings=settings, endpoint=_endpoint())

    assert identity.user_id == 1001
    assert identity.username == "afkme"
    state_path = tmp_path / "profiles/.system/channels/personal-user/telethon_user_state.json"
    assert state_path.exists() is False


async def test_logout_telethon_endpoint_skips_network_when_policy_disallows_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout should still clear local session when profile policy forbids MTProto."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_logout.db'}",
    )
    await _seed_profile(settings, allow_mtproto=False)
    state_path = tmp_path / "profiles/.system/channels/personal-user/telethon_user_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")
    client_created = False

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string="session",
            phone="+79990000000",
        )

    async def _fake_delete_secret(**kwargs: object) -> bool:
        _ = kwargs
        return True

    def _fake_create_client(**kwargs: object) -> object:
        nonlocal client_created
        client_created = True
        return _FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.delete_telethon_secret",
        _fake_delete_secret,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        _fake_create_client,
    )

    payload = await logout_telethon_endpoint(settings=settings, endpoint=_endpoint())

    assert payload["logged_out"] is False
    assert payload["network_logout_skipped"] is True
    assert payload["session_removed"] is True
    assert client_created is False
    assert state_path.exists() is False


async def test_authorize_telethon_endpoint_normalizes_phone_and_notifies_delivery_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization should normalize phone and tell the operator where to expect the code."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_authorize.db'}",
    )
    await _seed_profile(settings, allow_mtproto=True)
    fake_client = _FakeTelethonClient()
    notifications: list[str] = []
    saved_secrets: list[tuple[str, str]] = []

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string=None,
            phone="79990000000",
        )

    async def _fake_upsert_secret(**kwargs: object) -> None:
        saved_secrets.append((str(kwargs["credential_name"]), str(kwargs["secret_value"])))

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.import_telethon",
        lambda: SimpleNamespace(
            session_password_needed_error=RuntimeError,
            phone_code_invalid_error=ValueError,
            phone_code_expired_error=TimeoutError,
            phone_number_invalid_error=TypeError,
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth._client_session_string",
        lambda client: "session-string",
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.upsert_telethon_secret",
        _fake_upsert_secret,
    )
    async def _fake_persist_identity_state(**kwargs: object) -> None:
        _ = kwargs

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.persist_telethon_identity_state",
        _fake_persist_identity_state,
    )

    prompts = iter(["12345"])

    def _prompt(label: str, hide_input: bool) -> str:
        _ = (label, hide_input)
        return next(prompts)

    result = await authorize_telethon_endpoint(
        settings=settings,
        endpoint=_endpoint(),
        prompt=_prompt,
        notify=notifications.append,
    )

    assert result.session_string_saved is True
    assert fake_client.requested_phone == "+79990000000"
    assert fake_client.sign_in_calls == [{"phone": "+79990000000", "code": "12345"}]
    assert notifications
    assert "Primary delivery: Telegram app chat from Telegram/777000." in notifications[0]
    assert "Telegram timeout hint: 60s" in notifications[0]
    assert "may allow SMS next" in notifications[0]
    assert ("session_string", "session-string") in saved_secrets
    assert ("phone", "+79990000000") in saved_secrets


async def test_authorize_telethon_endpoint_via_qr_renders_qr_and_saves_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QR authorization should notify with QR details and persist the session string."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_authorize_qr.db'}",
    )
    await _seed_profile(settings, allow_mtproto=True)
    fake_client = _FakeTelethonClient()
    notifications: list[str] = []
    saved_secrets: list[tuple[str, str]] = []

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string=None,
            phone="+79990000000",
        )

    async def _fake_upsert_secret(**kwargs: object) -> None:
        saved_secrets.append((str(kwargs["credential_name"]), str(kwargs["secret_value"])))

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.import_telethon",
        lambda: SimpleNamespace(
            session_password_needed_error=RuntimeError,
            phone_code_invalid_error=ValueError,
            phone_code_expired_error=TimeoutError,
            phone_number_invalid_error=TypeError,
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth._client_session_string",
        lambda client: "session-string",
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.render_terminal_qr",
        lambda data: "QR-CODE",
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.upsert_telethon_secret",
        _fake_upsert_secret,
    )

    async def _fake_persist_identity_state(**kwargs: object) -> None:
        _ = kwargs

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.persist_telethon_identity_state",
        _fake_persist_identity_state,
    )

    def _prompt(label: str, hide_input: bool) -> str:
        raise AssertionError(f"Unexpected prompt: {label=} {hide_input=}")

    result = await authorize_telethon_endpoint(
        settings=settings,
        endpoint=_endpoint(),
        prompt=_prompt,
        notify=notifications.append,
        qr=True,
    )

    assert result.session_string_saved is True
    assert result.method == "qr"
    assert fake_client.qr_wait_called is True
    assert notifications
    assert "QR login requested for Telethon." in notifications[0]
    assert "tg://login?token=qr-token" in notifications[0]
    assert "QR-CODE" in notifications[0]
    assert ("session_string", "session-string") in saved_secrets
    assert ("phone", "+79990000000") in saved_secrets


async def test_authorize_telethon_endpoint_maps_invalid_code_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid Telegram login codes should raise a stable AFKBOT error."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_authorize_invalid_code.db'}",
    )
    await _seed_profile(settings, allow_mtproto=True)
    fake_client = _FakeTelethonClient()

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string=None,
            phone="+79990000000",
        )

    async def _fake_sign_in(client: object, **kwargs: str) -> None:
        _ = (client, kwargs)
        raise ValueError("bad code")

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.import_telethon",
        lambda: SimpleNamespace(
            session_password_needed_error=RuntimeError,
            phone_code_invalid_error=ValueError,
            phone_code_expired_error=TimeoutError,
            phone_number_invalid_error=TypeError,
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth._client_sign_in",
        _fake_sign_in,
    )

    prompts = iter(["12345"])

    def _prompt(label: str, hide_input: bool) -> str:
        _ = (label, hide_input)
        return next(prompts)

    with pytest.raises(Exception) as exc_info:
        await authorize_telethon_endpoint(
            settings=settings,
            endpoint=_endpoint(),
            prompt=_prompt,
            notify=None,
        )
    assert getattr(exc_info.value, "error_code", None) == "telethon_code_invalid"


async def test_authorize_telethon_endpoint_maps_real_telethon_invalid_code_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real Telethon PhoneCodeInvalidError should be wrapped into AFKBOT error codes."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_authorize_real_invalid_code.db'}",
    )
    await _seed_profile(settings, allow_mtproto=True)
    fake_client = _FakeTelethonClient()
    pytest.importorskip("telethon")
    telethon_errors = importlib.import_module("telethon.errors")

    async def _fake_resolve_credentials(**kwargs: object) -> object:
        _ = kwargs
        return SimpleNamespace(
            api_id=12345,
            api_hash="hash",
            session_string=None,
            phone="+79990000000",
        )

    async def _fake_sign_in(client: object, **kwargs: str) -> None:
        _ = (client, kwargs)
        raise telethon_errors.PhoneCodeInvalidError(request=None)

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.resolve_telethon_credentials",
        _fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth.create_telethon_client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.auth._client_sign_in",
        _fake_sign_in,
    )

    prompts = iter(["12345"])

    def _prompt(label: str, hide_input: bool) -> str:
        _ = (label, hide_input)
        return next(prompts)

    with pytest.raises(Exception) as exc_info:
        await authorize_telethon_endpoint(
            settings=settings,
            endpoint=_endpoint(),
            prompt=_prompt,
            notify=None,
        )
    assert getattr(exc_info.value, "error_code", None) == "telethon_code_invalid"
