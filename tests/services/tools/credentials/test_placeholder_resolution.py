"""Credential placeholder resolution scenarios for tool plugins."""

from __future__ import annotations

from typing import cast

from pytest import MonkeyPatch

from afkbot.services.tools.base import ToolContext
from tests.services.tools.credentials._harness import (
    create_credential,
    prepare_credentials_tools,
)


async def test_bash_exec_resolves_credential_placeholders(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="global",
        profile_name="default",
        credential_slug="bash_token",
        value="bash-secret-42",
    )

    list_tool = registry.get("credentials.list")
    bash_tool = registry.get("bash.exec")
    assert list_tool is not None
    assert bash_tool is not None

    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "global",
            "profile_name": "default",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    listed = await list_tool.execute(ctx, list_params)
    assert listed.ok is True
    env_key = cast(list[dict[str, object]], listed.payload["bindings"])[0]["ENV_KEY"]

    cred_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf '%s' '${{CRED:global/default/bash_token}}'",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    cred_result = await bash_tool.execute(ctx, cred_params)
    assert cred_result.ok is True
    assert cred_result.payload["stdout"] == "[REDACTED]"
    assert cred_result.payload["cmd"] == "printf '%s' '${{CRED:global/default/bash_token}}'"

    env_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": f"printf '%s' '${{{env_key}}}'",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    env_result = await bash_tool.execute(ctx, env_params)
    assert env_result.ok is True
    assert env_result.payload["stdout"] == "[REDACTED]"
    assert env_result.payload["cmd"] == f"printf '%s' '${{{env_key}}}'"

    missing_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf '%s' '${{CRED:global/default/missing_secret}}'",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    missing = await bash_tool.execute(ctx, missing_params)
    assert missing.ok is False
    assert missing.error_code == "credentials_missing"
    assert missing.metadata["credential_name"] == "missing_secret"

    await engine.dispose()


async def test_bash_exec_redacts_live_progress_preview_lines(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    """bash.exec live progress should redact resolved credential values before rendering."""

    # Arrange
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    try:
        progress_events: list[dict[str, object]] = []

        async def _capture_progress(payload: dict[str, object]) -> None:
            progress_events.append({str(key): value for key, value in payload.items()})

        ctx = ToolContext(
            profile_id="default",
            session_id="s-1",
            run_id=1,
            progress_callback=_capture_progress,
        )
        await create_credential(
            registry=registry,
            settings=settings,
            ctx=ctx,
            app_name="global",
            profile_name="default",
            credential_slug="bash_token",
            value="bash-secret-42",
        )

        bash_tool = registry.get("bash.exec")
        assert bash_tool is not None
        params = bash_tool.parse_params(
            {
                "profile_key": "default",
                "cmd": (
                    "printf '%s\\n' '${{CRED:global/default/bash_token}}'; "
                    "sleep 0.2; "
                    "printf 'done\\n'"
                ),
                "cwd": ".",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )

        # Act
        result = await bash_tool.execute(ctx, params)

        # Assert
        assert result.ok is True
        assert "bash-secret-42" not in str(result.payload["stdout"])
        preview_lines = [
            str(line)
            for event in progress_events
            for line in list(event.get("preview_lines") or [])
        ]
        assert preview_lines
        assert all("bash-secret-42" not in line for line in preview_lines)
        assert any("[REDACTED]" in line for line in preview_lines)
    finally:
        await engine.dispose()


async def test_http_request_resolves_credential_placeholders(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="http",
        profile_name="default",
        credential_slug="http_api_key",
        value="http-secret-99",
    )

    list_tool = registry.get("credentials.list")
    http_tool = registry.get("http.request")
    assert list_tool is not None
    assert http_tool is not None

    list_params = list_tool.parse_params(
        {
            "profile_key": "default",
            "app_name": "http",
            "profile_name": "default",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    listed = await list_tool.execute(ctx, list_params)
    assert listed.ok is True
    env_key = cast(list[dict[str, object]], listed.payload["bindings"])[0]["ENV_KEY"]

    captured: dict[str, object] = {}

    async def _fake_request(*, payload: object, headers: object) -> dict[str, object]:
        captured["url"] = getattr(payload, "url")
        captured["body"] = getattr(payload, "body")
        captured["headers"] = headers
        return {
            "method": "GET",
            "url": str(getattr(payload, "url")),
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": '{"ok":true}',
            "json": {"ok": True},
        }

    monkeypatch.setattr(http_tool, "_perform_request", _fake_request)
    params = http_tool.parse_params(
        {
            "profile_key": "default",
            "method": "GET",
            "url": "https://example.com?k=${{CRED:http/default/http_api_key}}",
            "headers": {
                "X-Token": f"${{{env_key}}}",
            },
            "body": "token=${{CRED:http_api_key}}",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    result = await http_tool.execute(ctx, params)
    assert result.ok is True
    assert captured["url"] == "https://example.com?k=http-secret-99"
    assert captured["body"] == "token=http-secret-99"
    assert cast(dict[str, str], captured["headers"])["X-Token"] == "http-secret-99"
    assert result.payload["url"] == "https://example.com?k=${{CRED:http/default/http_api_key}}"

    missing_params = http_tool.parse_params(
        {
            "profile_key": "default",
            "method": "GET",
            "url": "https://example.com?k=${{CRED:http/default/http_missing}}",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    missing = await http_tool.execute(ctx, missing_params)
    assert missing.ok is False
    assert missing.error_code == "credentials_missing"
    assert missing.metadata["credential_name"] == "http_missing"

    await engine.dispose()


async def test_placeholder_resolution_rejects_cross_app_selectors(
    tmp_path,
    monkeypatch: MonkeyPatch,
) -> None:
    settings, engine, _, registry = await prepare_credentials_tools(tmp_path, monkeypatch)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)
    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="http",
        profile_name="default",
        credential_slug="http_api_key",
        value="http-secret-99",
    )
    await create_credential(
        registry=registry,
        settings=settings,
        ctx=ctx,
        app_name="global",
        profile_name="default",
        credential_slug="global_token",
        value="global-secret-11",
    )

    bash_tool = registry.get("bash.exec")
    http_tool = registry.get("http.request")
    assert bash_tool is not None
    assert http_tool is not None

    bash_cross_params = bash_tool.parse_params(
        {
            "profile_key": "default",
            "cmd": "printf '%s' '${{CRED:http/default/http_api_key}}'",
            "cwd": ".",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    bash_cross = await bash_tool.execute(ctx, bash_cross_params)
    assert bash_cross.ok is False
    assert bash_cross.error_code == "credentials_scope_violation"

    http_cross_params = http_tool.parse_params(
        {
            "profile_key": "default",
            "method": "GET",
            "url": "https://example.com?k=${{CRED:global/default/global_token}}",
        },
        default_timeout_sec=settings.tool_timeout_default_sec,
        max_timeout_sec=settings.tool_timeout_max_sec,
    )
    http_cross = await http_tool.execute(ctx, http_cross_params)
    assert http_cross.ok is False
    assert http_cross.error_code == "credentials_scope_violation"

    await engine.dispose()
