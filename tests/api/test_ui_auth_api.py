"""API tests for AFKBOT UI auth protection around mounted plugins."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from afkbot.api.app import create_app
from afkbot.services.plugins import get_plugin_service
from afkbot.services.ui_auth import hash_ui_auth_password, upsert_ui_auth
from afkbot.settings import get_settings


def _write_api_demo_plugin(root: Path) -> None:
    (root / ".afkbot-plugin").mkdir(parents=True, exist_ok=True)
    (root / "python/afkbot_plugin_demo").mkdir(parents=True, exist_ok=True)
    (root / "web/dist").mkdir(parents=True, exist_ok=True)
    (root / ".afkbot-plugin/plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "demo",
                "name": "Demo Plugin",
                "version": "0.1.0",
                "afkbot_version": "*",
                "kind": "embedded",
                "entrypoint": "afkbot_plugin_demo.plugin:register",
                "auth": {"operator_required": True},
                "capabilities": {"api_router": True, "static_web": True, "lifecycle": False},
                "mounts": {"api_prefix": "/v1/plugins/demo", "web_prefix": "/plugins/demo"},
                "paths": {"python_root": "python", "web_root": "web/dist"},
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "python/afkbot_plugin_demo/__init__.py").write_text("", encoding="utf-8")
    (root / "python/afkbot_plugin_demo/plugin.py").write_text(
        """
from __future__ import annotations

from fastapi import APIRouter

from afkbot.services.plugins.runtime_registry import PluginRuntimeRegistry


def register(registry: PluginRuntimeRegistry) -> None:
    router = APIRouter(prefix=registry.manifest.mounts.api_prefix or "/v1/plugins/demo")

    @router.get("/ping")
    async def ping():
        return {"plugin": "demo"}

    registry.register_router(router)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "web/dist/index.html").write_text("<html><body>demo mounted</body></html>\n", encoding="utf-8")


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
        plugins_response = client.get("/v1/plugins")
        api_response = client.get("/v1/plugins/demo/ping")
        web_response = client.get("/plugins/demo/?tab=skills&profile=default", follow_redirects=False)

        assert plugins_response.status_code == 401
        assert plugins_response.json()["error_code"] == "ui_auth_required"
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
        plugins_authed = client.get("/v1/plugins")
        api_authed = client.get("/v1/plugins/demo/ping")
        web_authed = client.get("/plugins/demo/")

        assert session_response.status_code == 200
        assert session_response.json()["authenticated"] is True
        assert plugins_authed.status_code == 200
        assert api_authed.status_code == 200
        assert api_authed.json() == {"plugin": "demo"}
        assert web_authed.status_code == 200
        assert "demo mounted" in web_authed.text

        logout_response = client.post("/v1/auth/logout")
        api_after_logout = client.get("/v1/plugins/demo/ping")

        assert logout_response.status_code == 200
        assert api_after_logout.status_code == 401
