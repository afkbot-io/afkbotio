"""Tests for `afk cloud` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.cloud_remote import infer_cloud_api_url_from_public_url, resolve_cloud_api_url
from afkbot.services.setup.runtime_store import read_runtime_config, read_runtime_secrets
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'cloud.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def test_cloud_connect_verifies_and_stores_remote_token(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk cloud connect` should persist metadata and store the token as a runtime secret."""

    _prepare_env(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    def _fake_verify_remote_bot_connection(*, api_url: str, public_url: str, token: str) -> dict[str, object]:
        captured.update({"api_url": api_url, "public_url": public_url, "token": token})
        return {
            "bot": {
                "id": "bot-1",
                "name": "Ops bot",
                "organization_id": "org-1",
                "public_url": public_url,
                "status": "running",
            },
            "token": {"name": "Local CLI", "scopes": ["remote_connect"], "expires_at": None},
            "profile_config": {"instructions": "Stay brief.", "skills": ["github"]},
        }

    monkeypatch.setattr("afkbot.cli.commands.cloud.verify_remote_bot_connection", _fake_verify_remote_bot_connection)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "cloud",
            "connect",
            "--url",
            "http://q8m2xk4p.cloud.afkbot.local/bot/bot-1",
            "--token",
            "afkbt_prefix_secret",
            "--api-url",
            "http://127.0.0.1:8000/api/v1",
            "--name",
            "ops",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["connection"]["name"] == "ops"
    assert payload["connection"]["bot_id"] == "bot-1"
    assert payload["connection"]["bot_name"] == "Ops bot"
    assert "afkbt_prefix_secret" not in result.stdout
    assert captured["token"] == "afkbt_prefix_secret"

    settings = get_settings()
    config = read_runtime_config(settings)
    secrets = read_runtime_secrets(settings)
    assert config["cloud_remote_connections"]["ops"]["public_url"] == "http://q8m2xk4p.cloud.afkbot.local/bot/bot-1"
    assert config["cloud_remote_connections"]["ops"]["profile_config"]["instructions"] == "Stay brief."
    assert secrets["cloud_remote_token:ops"] == "afkbt_prefix_secret"


def test_cloud_connect_infers_api_url_from_public_bot_url(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk cloud connect` should use the Cloud dashboard API by default."""

    _prepare_env(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    def _fake_verify_remote_bot_connection(*, api_url: str, public_url: str, token: str) -> dict[str, object]:
        captured.update({"api_url": api_url, "public_url": public_url, "token": token})
        return {
            "bot": {
                "id": "bot-1",
                "name": "Ops bot",
                "organization_id": "org-1",
                "public_url": public_url,
                "status": "running",
            },
            "token": {"scopes": ["remote_connect"]},
            "profile_config": {},
        }

    monkeypatch.setattr("afkbot.cli.commands.cloud.verify_remote_bot_connection", _fake_verify_remote_bot_connection)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "cloud",
            "connect",
            "--url",
            "https://q8m2xk4p.cloud.afkbot.io/bot/bot-1",
            "--token",
            "afkbt_prefix_secret",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["api_url"] == "https://cloud.afkbot.io/api/v1"


def test_cloud_api_url_inference_preserves_local_scheme_and_port() -> None:
    """Local wildcard domains should infer the matching local Cloud API origin."""

    assert (
        infer_cloud_api_url_from_public_url("http://q8m2xk4p.cloud.afkbot.local:8080/bot/bot-1")
        == "http://cloud.afkbot.local:8080/api/v1"
    )
    assert resolve_cloud_api_url(public_url="https://q8m2xk4p.cloud.afkbot.io/bot/bot-1") == "https://cloud.afkbot.io/api/v1"


def test_cloud_api_url_rejects_insecure_non_local_http() -> None:
    """Cloud tokens must not be sent to plain HTTP outside local development."""

    with pytest.raises(Exception) as exc_info:
        resolve_cloud_api_url("http://cloud.example.com/api/v1")

    assert getattr(exc_info.value, "error_code", "") == "cloud_api_url_insecure"


def test_cloud_list_outputs_saved_connections(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk cloud list --json` should list saved connection metadata without tokens."""

    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "afkbot.cli.commands.cloud.verify_remote_bot_connection",
        lambda **kwargs: {
            "bot": {
                "id": "bot-1",
                "name": "Ops bot",
                "organization_id": "org-1",
                "public_url": kwargs["public_url"],
                "status": "running",
            },
            "token": {"scopes": ["remote_connect"]},
            "profile_config": {},
        },
    )
    runner = CliRunner()
    connect_result = runner.invoke(
        app,
        [
            "cloud",
            "connect",
            "--url",
            "http://q8m2xk4p.cloud.afkbot.local/bot/bot-1",
            "--token",
            "afkbt_prefix_secret",
            "--name",
            "ops",
            "--json",
        ],
    )
    assert connect_result.exit_code == 0

    result = runner.invoke(app, ["cloud", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["connections"][0]["name"] == "ops"
    assert "afkbt_prefix_secret" not in result.stdout
