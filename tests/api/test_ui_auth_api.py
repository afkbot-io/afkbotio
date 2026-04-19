"""API tests for AFKBOT UI auth protection around mounted plugins."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.plugins import get_plugin_service
from afkbot.services.ui_auth import hash_ui_auth_password, upsert_ui_auth
from afkbot.services.setup.runtime_store import read_runtime_secrets, write_runtime_secrets
from afkbot.settings import get_settings


def _write_api_demo_plugin(
    root: Path,
    *,
    plugin_id: str = "demo",
    operator_required: bool = True,
    api_prefix: str | None = None,
    web_prefix: str | None = None,
) -> None:
    resolved_api_prefix = api_prefix or f"/v1/plugins/{plugin_id}"
    resolved_web_prefix = web_prefix or f"/plugins/{plugin_id}"
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    package_name = f"afkbot_plugin_{plugin_id}"
    (root / f"python/{package_name}").mkdir(parents=True, exist_ok=True)
    (root / "web/dist").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": plugin_id,
                "name": f"{plugin_id.title()} Plugin",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": f"{package_name}.plugin:register",
                "auth": {"operator_required": operator_required},
                "capabilities": {"api_router": True, "static_web": True, "lifecycle": False},
                "mounts": {"api_prefix": resolved_api_prefix, "web_prefix": resolved_web_prefix},
                "paths": {"python_root": "python", "web_root": "web/dist"},
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / f"python/{package_name}/__init__.py").write_text("", encoding="utf-8")
    (root / f"python/{package_name}/plugin.py").write_text(
        f"""
from __future__ import annotations

from fastapi import APIRouter

from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry


def register(registry: PluginRuntimeRegistry) -> None:
    router = APIRouter(prefix=registry.manifest.mounts.api_prefix or {resolved_api_prefix!r})

    @router.get("/ping")
    async def ping():
        return {{"plugin": {plugin_id!r}}}

    registry.register_router(router)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "web/dist/index.html").write_text(
        f"<html><body>{plugin_id} mounted</body></html>\n",
        encoding="utf-8",
    )


def test_protected_plugin_surfaces_require_login_and_accept_cookie(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Protected plugin web/API surfaces should enforce login and then allow cookie-auth access."""

    source_root = tmp_path / "demo-plugin-src"
    _write_api_demo_plugin(source_root)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with TestClient(create_app()) as client:
        session_before_login = client.get("/v1/auth/session")
        plugins_response = client.get("/v1/plugins")
        api_response = client.get("/v1/plugins/demo/ping")
        web_response = client.get("/plugins/demo/?tab=skills&profile=default", follow_redirects=False)

        assert session_before_login.status_code == 200
        assert session_before_login.json() == {
            "authenticated": False,
            "session": None,
            "auth": {
                "mode": "password",
                "configured": True,
            },
        }
        assert plugins_response.status_code == 200
        assert api_response.status_code == 401
        assert api_response.json()["error_code"] == "ui_auth_required"
        assert web_response.status_code == 303
        assert web_response.headers["location"].startswith("/auth/login?")
        assert (
            "next=%2Fplugins%2Fdemo%2F%3Ftab%3Dskills%26profile%3Ddefault"
            in web_response.headers["location"]
        )

        bad_login = client.post(
            "/v1/auth/login",
            json={"username": "operator", "password": "wrong"},
        )
        assert bad_login.status_code == 401
        assert bad_login.json()["error_code"] == "ui_auth_invalid_credentials"

        good_login = client.post(
            "/v1/auth/login",
            json={"username": "operator", "password": "correct-horse-battery"},
        )
        assert good_login.status_code == 200
        assert good_login.json()["ok"] is True

        session_response = client.get("/v1/auth/session")
        api_authed = client.get("/v1/plugins/demo/ping")
        web_authed = client.get("/plugins/demo/")

        assert session_response.status_code == 200
        assert session_response.json()["authenticated"] is True
        assert session_response.json()["auth"] == {"mode": "password", "configured": True}
        assert api_authed.status_code == 200
        assert api_authed.json() == {"plugin": "demo"}
        assert web_authed.status_code == 200
        assert "demo mounted" in web_authed.text

        logout_response = client.post("/v1/auth/logout")
        api_after_logout = client.get("/v1/plugins/demo/ping")

        assert logout_response.status_code == 200
        assert api_after_logout.status_code == 401


def test_unprotected_plugin_surfaces_stay_public_when_ui_auth_is_enabled(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Auth-enabled runtimes must not block plugin surfaces that never opted in."""

    source_root = tmp_path / "public-plugin-src"
    _write_api_demo_plugin(source_root, plugin_id="publicdemo", operator_required=False)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with TestClient(create_app()) as client:
        api_response = client.get("/v1/plugins/publicdemo/ping")
        web_response = client.get("/plugins/publicdemo/")

        assert api_response.status_code == 200
        assert api_response.json() == {"plugin": "publicdemo"}
        assert web_response.status_code == 200
        assert "publicdemo mounted" in web_response.text


def test_explicitly_protected_plugin_ids_guard_matching_api_and_web_prefixes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`--protected-plugin-id` should protect the matching plugin mounts even without manifest opt-in."""

    source_root = tmp_path / "config-protected-plugin-src"
    _write_api_demo_plugin(
        source_root,
        plugin_id="configdemo",
        operator_required=False,
        api_prefix="/internal/configdemo",
        web_prefix="/ui/configdemo",
    )
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=("configdemo",),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with TestClient(create_app()) as client:
        api_response = client.get("/internal/configdemo/ping")
        web_response = client.get("/ui/configdemo/", follow_redirects=False)

        assert api_response.status_code == 401
        assert web_response.status_code == 303


def test_operator_required_plugin_with_custom_prefixes_is_protected(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Manifest operator auth must follow custom API and web mount prefixes."""

    source_root = tmp_path / "custom-plugin-src"
    _write_api_demo_plugin(
        source_root,
        plugin_id="customdemo",
        operator_required=True,
        api_prefix="/internal/demo",
        web_prefix="/ui/demo",
    )
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()
    settings = get_settings()
    get_plugin_service(settings).install(source=str(source_root))

    with TestClient(create_app()) as client:
        api_response = client.get("/internal/demo/ping")
        web_response = client.get("/ui/demo/?tab=skills", follow_redirects=False)

        assert api_response.status_code == 401
        assert api_response.json()["error_code"] == "ui_auth_required"
        assert web_response.status_code == 303
        assert "next=%2Fui%2Fdemo%2F%3Ftab%3Dskills" in web_response.headers["location"]

        good_login = client.post(
            "/v1/auth/login",
            json={"username": "operator", "password": "correct-horse-battery"},
        )
        assert good_login.status_code == 200
        assert client.get("/internal/demo/ping").status_code == 200
        assert client.get("/ui/demo/").status_code == 200


def test_login_uses_password_verifier_even_for_wrong_username(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Wrong usernames should still pay the password verification path."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()

    import afkbot.api.routes_auth as routes_auth

    calls: list[tuple[str, str | None]] = []
    original_verify = routes_auth.verify_ui_auth_password

    def tracked_verify(password: str, encoded_hash: str | None) -> bool:
        calls.append((password, encoded_hash))
        return original_verify(password, encoded_hash)

    monkeypatch.setattr(routes_auth, "verify_ui_auth_password", tracked_verify)

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/auth/login",
            json={"username": "wrong-user", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert len(calls) == 1


def test_login_auto_heals_missing_cookie_key(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Login should regenerate a missing cookie key instead of failing with 500."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )

    secrets = dict(read_runtime_secrets(settings))
    secrets.pop("ui_auth_cookie_key", None)
    write_runtime_secrets(settings, secrets=secrets)
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/auth/login",
            json={"username": "operator", "password": "correct-horse-battery"},
        )

    assert response.status_code == 200
    get_settings.cache_clear()
    regenerated_secrets = read_runtime_secrets(get_settings())
    assert regenerated_secrets["ui_auth_cookie_key"]


def test_login_page_does_not_prefill_operator_username(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Anonymous login page must not disclose the configured operator username."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    upsert_ui_auth(
        settings,
        username="operator",
        password_hash=hash_ui_auth_password("correct-horse-battery"),
        session_ttl_sec=3600,
        idle_ttl_sec=900,
        login_rate_limit_window_sec=600,
        login_rate_limit_max_attempts=5,
        lockout_sec=600,
        protected_plugin_ids=(),
        trust_proxy_headers=False,
    )
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        response = client.get("/auth/login")

    assert response.status_code == 200
    assert 'id="username"' in response.text
    assert 'value=""' in response.text
    assert 'value="operator"' not in response.text
