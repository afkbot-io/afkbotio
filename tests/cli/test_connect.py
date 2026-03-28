"""Tests for `afk connect` command."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect import ConnectIssueResult
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'connect.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    monkeypatch.setenv("AFKBOT_RUNTIME_HOST", "127.0.0.9")
    monkeypatch.setenv("AFKBOT_RUNTIME_PORT", "19080")
    get_settings.cache_clear()


def test_connect_json_output(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk connect --json` should emit deterministic payload."""

    _prepare_env(tmp_path, monkeypatch)

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        assert profile_id == "default"
        assert session_id == "desktop-s"
        assert base_url == "http://127.0.0.1:8081"
        assert ttl_sec == 120
        assert allow_diagnostics is False
        assert claim_pin is None
        assert context_overrides is None
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "connect",
            "--host",
            "http://127.0.0.1:8081",
            "--session",
            "desktop-s",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload == {
        "ok": True,
        "connect_url": "afk://connect?claim_token=abc",
        "expires_at": "2026-03-04T12:00:00Z",
        "profile_id": "default",
        "session_id": "desktop-s",
    }


def test_connect_uses_runtime_default_host(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Without `--host`, command should use runtime host and `runtime_port + 1`."""

    _prepare_env(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        captured.update(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "base_url": base_url,
                "ttl_sec": ttl_sec,
                "allow_diagnostics": allow_diagnostics,
                "claim_pin": claim_pin,
                "context_overrides": context_overrides,
            }
        )
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    runner = CliRunner()
    result = runner.invoke(app, ["connect", "--json"])

    assert result.exit_code == 0
    assert captured == {
        "profile_id": "default",
        "session_id": "desktop-session",
        "base_url": "http://127.0.0.9:19081",
        "ttl_sec": 120,
        "allow_diagnostics": False,
        "claim_pin": None,
        "context_overrides": None,
    }


def test_connect_plain_output_warns_for_loopback_base_url(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Plain-text connect output should warn when the URL targets loopback only."""

    _prepare_env(tmp_path, monkeypatch)

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    runner = CliRunner()

    result = runner.invoke(app, ["connect"])

    assert result.exit_code == 0
    assert "works only on the same device" in result.stderr
    assert "afk://connect?claim_token=abc" in result.stdout


def test_connect_rejects_invalid_profile_id(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Connect CLI should reject invalid profile ids before issuing tokens."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["connect", "--profile", "Default", "--json"])

    assert result.exit_code == 2
    assert "Invalid profile id: Default" in result.stderr


def test_connect_prefers_saved_public_chat_api_url(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Saved public chat/api URL should override runtime host fallback when --host is omitted."""

    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setenv("AFKBOT_PUBLIC_CHAT_API_URL", "https://chat.example.com")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        captured.update(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "base_url": base_url,
                "ttl_sec": ttl_sec,
                "allow_diagnostics": allow_diagnostics,
                "claim_pin": claim_pin,
                "context_overrides": context_overrides,
            }
        )
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    runner = CliRunner()
    result = runner.invoke(app, ["connect", "--json"])

    assert result.exit_code == 0
    assert captured == {
        "profile_id": "default",
        "session_id": "desktop-session",
        "base_url": "https://chat.example.com",
        "ttl_sec": 120,
        "allow_diagnostics": False,
        "claim_pin": None,
        "context_overrides": None,
    }


def test_connect_resolves_binding_target(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """`afk connect` should resolve effective profile/session via persisted binding rules."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "sales",
            "--name",
            "Sales",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert add_result.exit_code == 0

    binding_result = runner.invoke(
        app,
        [
            "profile",
            "binding",
            "set",
            "desktop-sales",
            "--transport",
            "desktop",
            "--profile-id",
            "sales",
            "--session-policy",
            "per-thread",
            "--peer-id",
            "workspace-7",
        ],
    )
    assert binding_result.exit_code == 0

    captured: dict[str, object] = {}

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        captured.update(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "base_url": base_url,
                "ttl_sec": ttl_sec,
                "allow_diagnostics": allow_diagnostics,
                "claim_pin": claim_pin,
                "context_overrides": context_overrides,
            }
        )
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    result = runner.invoke(
        app,
        [
            "connect",
            "--resolve-binding",
            "--transport",
            "desktop",
            "--peer-id",
            "workspace-7",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert captured["profile_id"] == "sales"
    assert captured["session_id"] == "profile:sales:chat:workspace-7"
    assert captured["claim_pin"] is None
    assert captured["context_overrides"] is not None
    assert captured["context_overrides"].runtime_metadata == {
        "transport": "desktop",
        "peer_id": "workspace-7",
        "channel_binding": {
            "binding_id": "desktop-sales",
            "session_policy": "per-thread",
        },
    }


def test_connect_requires_transport_when_binding_resolution_enabled(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Binding-aware connect mode should require transport metadata."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["connect", "--resolve-binding"])

    assert result.exit_code != 0
    assert "--transport is required with --resolve-binding" in result.stderr


def test_connect_can_require_binding_match(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Strict binding mode should reject unresolved connect selectors."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "connect",
            "--resolve-binding",
            "--require-binding-match",
            "--transport",
            "desktop",
            "--peer-id",
            "workspace-7",
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert "No channel binding matched the provided target selectors." in result.stderr


def test_connect_can_generate_claim_pin(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """CLI should be able to generate and print an out-of-band claim PIN."""

    _prepare_env(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    async def _fake_issue_connect_url(
        *,
        profile_id: str,
        session_id: str,
        base_url: str,
        ttl_sec: int | None = None,
        allow_diagnostics: bool = False,
        claim_pin: str | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> ConnectIssueResult:
        captured.update(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "base_url": base_url,
                "ttl_sec": ttl_sec,
                "allow_diagnostics": allow_diagnostics,
                "claim_pin": claim_pin,
                "context_overrides": context_overrides,
            }
        )
        return ConnectIssueResult(
            connect_url="afk://connect?claim_token=abc",
            expires_at=datetime(2026, 3, 4, 12, 0, 0, tzinfo=UTC),
            profile_id=profile_id,
            session_id=session_id,
            base_url=base_url,
            claim_pin=claim_pin,
        )

    monkeypatch.setattr("afkbot.cli.commands.connect.generate_claim_pin", lambda: "246810")
    monkeypatch.setattr("afkbot.cli.commands.connect.issue_connect_url", _fake_issue_connect_url)
    runner = CliRunner()

    result = runner.invoke(app, ["connect", "--generate-claim-pin", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["claim_pin"] == "246810"
    assert captured["claim_pin"] == "246810"


def test_connect_rejects_conflicting_claim_pin_flags(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """CLI should reject mutually exclusive explicit and generated claim PIN options."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["connect", "--claim-pin", "2468", "--generate-claim-pin"])

    assert result.exit_code == 2
    assert "--claim-pin and --generate-claim-pin cannot be used together." in result.stderr


def test_connect_external_binding_resolution_is_strict_by_default(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Connect should fail closed for unresolved external transport bindings by default."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "connect",
            "--resolve-binding",
            "--transport",
            "telegram",
            "--peer-id",
            "42",
        ],
    )

    assert result.exit_code != 0
    assert "No channel binding matched the provided target selectors." in result.stderr
