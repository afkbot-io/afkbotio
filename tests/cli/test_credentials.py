"""Tests for `afk credentials` command group."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli_credentials.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def test_credentials_create_and_list_json(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk credentials create/list` should return deterministic JSON payloads."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    create_result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--slug",
            "telegram_token",
            "--value-stdin",
            "--replace-existing",
            "--json",
        ],
        input="super-secret-token\n",
    )
    assert create_result.exit_code == 0
    create_payload = json.loads(create_result.stdout.strip())
    assert create_payload["ok"] is True
    binding = create_payload["binding"]
    assert binding["APP_NAME"] == "telegram"
    assert binding["PROFILE_NAME"] == "work"
    assert binding["CREDENTIAL_SLUG"] == "telegram_token"
    assert binding["ENV_KEY"] == "CRED_TELEGRAM_WORK_TELEGRAM_TOKEN"
    assert "super-secret-token" not in create_result.stdout

    list_result = runner.invoke(
        app,
        [
            "credentials",
            "list",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--json",
        ],
    )
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout.strip())
    assert list_payload["ok"] is True
    assert list_payload["count"] == 1
    row = list_payload["bindings"][0]
    assert row["APP_NAME"] == "telegram"
    assert row["PROFILE_NAME"] == "work"
    assert row["CREDENTIAL_SLUG"] == "telegram_token"
    assert row["ENV_KEY"] == "CRED_TELEGRAM_WORK_TELEGRAM_TOKEN"

    list_all_result = runner.invoke(
        app,
        [
            "credentials",
            "list",
            "--profile",
            "default",
            "--json",
        ],
    )
    assert list_all_result.exit_code == 0
    list_all_payload = json.loads(list_all_result.stdout.strip())
    assert list_all_payload["ok"] is True
    assert list_all_payload["count"] == 1
    assert list_all_payload["bindings"][0]["APP_NAME"] == "telegram"


def test_credentials_create_value_stdin_strips_windows_newline_only(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`--value-stdin` should drop one trailing CRLF terminator without trimming real content."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    create_result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--slug",
            "telegram_token",
            "--value-stdin",
            "--replace-existing",
            "--json",
        ],
        input="secret-token\r\n",
    )
    assert create_result.exit_code == 0

    list_result = runner.invoke(
        app,
        [
            "credentials",
            "list",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--json",
        ],
    )
    assert list_result.exit_code == 0
    payload = json.loads(list_result.stdout.strip())
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["bindings"][0]["ENV_KEY"] == "CRED_TELEGRAM_WORK_TELEGRAM_TOKEN"


def test_credentials_update_and_delete_deterministic_errors(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`afk credentials update/delete` should expose deterministic success/error payloads."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    missing_update = runner.invoke(
        app,
        [
            "credentials",
            "update",
            "--profile",
            "default",
            "--app",
            "smtp",
            "--profile-name",
            "default",
            "--slug",
            "smtp_password",
            "--value-stdin",
            "--json",
        ],
        input="secret-1\n",
    )
    assert missing_update.exit_code == 1
    missing_payload = json.loads(missing_update.stdout.strip())
    assert missing_payload == {
        "ok": False,
        "error_code": "credentials_not_found",
        "reason": "Credential binding not found",
        "metadata": {},
    }

    create_result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "smtp",
            "--profile-name",
            "default",
            "--slug",
            "smtp_password",
            "--value-stdin",
            "--json",
        ],
        input="secret-1\n",
    )
    assert create_result.exit_code == 0

    update_result = runner.invoke(
        app,
        [
            "credentials",
            "update",
            "--profile",
            "default",
            "--app",
            "smtp",
            "--profile-name",
            "default",
            "--slug",
            "smtp_password",
            "--value-stdin",
            "--json",
        ],
        input="secret-2\n",
    )
    assert update_result.exit_code == 0
    update_payload = json.loads(update_result.stdout.strip())
    assert update_payload["ok"] is True

    delete_result = runner.invoke(
        app,
        [
            "credentials",
            "delete",
            "--profile",
            "default",
            "--app",
            "smtp",
            "--profile-name",
            "default",
            "--slug",
            "smtp_password",
            "--json",
        ],
    )
    assert delete_result.exit_code == 0
    delete_payload = json.loads(delete_result.stdout.strip())
    assert delete_payload == {
        "ok": True,
        "deleted": True,
        "app_name": "smtp",
        "profile_name": "default",
        "credential_slug": "smtp_password",
    }


def test_credentials_cli_rejects_insecure_value_arg(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Secrets must not be accepted from CLI argv `--value`."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--slug",
            "telegram_token",
            "--value",
            "super-secret-token",
        ],
    )
    assert result.exit_code != 0
    assert "Passing secrets via --value is disabled" in result.output


def test_credentials_list_includes_global_fallback_bindings_for_app_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI list for one app should include global fallback bindings visible to runtime."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    create_result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "global",
            "--profile-name",
            "default",
            "--slug",
            "smtp_password",
            "--value-stdin",
            "--json",
        ],
        input="secret-1\n",
    )
    assert create_result.exit_code == 0

    list_result = runner.invoke(
        app,
        [
            "credentials",
            "list",
            "--profile",
            "default",
            "--app",
            "smtp",
            "--profile-name",
            "default",
            "--json",
        ],
    )
    assert list_result.exit_code == 0

    payload = json.loads(list_result.stdout.strip())
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["bindings"][0]["APP_NAME"] == "global"
    assert payload["bindings"][0]["CREDENTIAL_SLUG"] == "smtp_password"


def test_credentials_default_output_is_human_readable(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default credentials CLI output should be readable text instead of JSON."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    create_result = runner.invoke(
        app,
        [
            "credentials",
            "create",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
            "--slug",
            "telegram_token",
            "--value-stdin",
        ],
        input="super-secret-token\n",
    )
    assert create_result.exit_code == 0
    assert "Credential saved:" in create_result.stdout
    assert "\"ok\"" not in create_result.stdout

    list_result = runner.invoke(
        app,
        [
            "credentials",
            "list",
            "--profile",
            "default",
            "--app",
            "telegram",
            "--profile-name",
            "work",
        ],
    )
    assert list_result.exit_code == 0
    assert "Found 1 credential binding(s)." in list_result.stdout
    assert "telegram/work/telegram_token" in list_result.stdout
