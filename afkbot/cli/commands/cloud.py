"""Cloud connection CLI commands."""

from __future__ import annotations

from dataclasses import asdict
import json
import time
from typing import Any

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.cloud_remote import (
    CloudRemoteError,
    list_remote_bot_connections,
    poll_remote_chat_messages,
    read_remote_profile_config,
    resolve_cloud_api_url,
    run_remote_lifecycle_action,
    save_remote_bot_connection,
    send_remote_chat_message,
    update_remote_profile_config,
    verify_remote_bot_connection,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register Cloud commands."""

    cloud_app = typer.Typer(
        help="Connect local AFKBOT to AFKBOT Cloud bots.",
        no_args_is_help=True,
    )

    @cloud_app.command("connect")
    def connect(
        url: str = typer.Option(..., "--url", help="Public bot URL from AFKBOT Cloud."),
        token: str | None = typer.Option(
            None,
            "--token",
            help="Remote connection token. Omit to enter it as a hidden prompt.",
        ),
        api_url: str | None = typer.Option(
            None,
            "--api-url",
            help="Cloud API base URL. Defaults to AFKBOT_CLOUD_API_URL, inferred Cloud URL, or production Cloud API.",
        ),
        name: str = typer.Option(
            "default",
            "--name",
            help="Local name for this remote bot connection.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Verify and save a remote AFKBOT Cloud connection."""

        try:
            resolved_api_url = resolve_cloud_api_url(api_url, public_url=url)
            resolved_token = token or typer.prompt("Cloud token", hide_input=True)
            payload = verify_remote_bot_connection(
                api_url=resolved_api_url,
                public_url=url,
                token=resolved_token,
            )
            connection = save_remote_bot_connection(
                settings=get_settings(),
                name=name,
                api_url=resolved_api_url,
                public_url=url,
                token=resolved_token,
                verification_payload=payload,
            )
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return

        if json_output:
            typer.echo(json.dumps({"ok": True, "connection": asdict(connection)}, ensure_ascii=True))
            return
        typer.echo(f"connected: {connection.name}")
        typer.echo(f"bot: {connection.bot_name or connection.bot_id}")
        typer.echo(f"url: {connection.public_url}")

    @cloud_app.command("list")
    def list_connections(
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """List saved remote AFKBOT Cloud connections."""

        connections = list_remote_bot_connections(settings=get_settings())
        if json_output:
            typer.echo(json.dumps({"ok": True, "connections": [asdict(item) for item in connections]}, ensure_ascii=True))
            return
        if not connections:
            typer.echo("No Cloud connections saved.")
            return
        for connection in connections:
            typer.echo(f"{connection.name}: {connection.bot_name or connection.bot_id} {connection.public_url}")

    @cloud_app.command("chat")
    def chat(
        message: str = typer.Option(..., "--message", "-m", help="Message to send to the Cloud bot."),
        connection: str = typer.Argument("default", help="Saved Cloud connection name."),
        profile: str = typer.Option("", "--profile", help="Optional runtime profile id."),
        wait: bool = typer.Option(
            True,
            "--wait/--no-wait",
            help="Poll for the first assistant reply after sending the message.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Send one chat message to a Cloud bot through a saved token."""

        try:
            payload = send_remote_chat_message(
                settings=get_settings(),
                connection_name=connection,
                content=message,
                profile_id=profile,
            )
            if wait:
                payload = _attach_remote_chat_replies(connection=connection, payload=payload)
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return

        if json_output:
            typer.echo(json.dumps({"ok": True, "message": payload}, ensure_ascii=True))
            return
        _render_chat_payload(payload)

    @cloud_app.command("start")
    def start(
        connection: str = typer.Argument("default", help="Saved Cloud connection name."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Start the remote Cloud runtime for a saved connection."""

        _run_lifecycle_command(connection=connection, action="start", json_output=json_output)

    @cloud_app.command("stop")
    def stop(
        connection: str = typer.Argument("default", help="Saved Cloud connection name."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Stop the remote Cloud runtime for a saved connection."""

        _run_lifecycle_command(connection=connection, action="stop", json_output=json_output)

    @cloud_app.command("restart")
    def restart(
        connection: str = typer.Argument("default", help="Saved Cloud connection name."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Restart the remote Cloud runtime for a saved connection."""

        _run_lifecycle_command(connection=connection, action="restart", json_output=json_output)

    @cloud_app.command("setup")
    def setup(
        connection: str = typer.Argument("default", help="Saved Cloud connection name."),
        instructions: str | None = typer.Option(None, "--instructions", help="Cloud bot instructions."),
        model: str | None = typer.Option(None, "--model", help="Cloud bot model id."),
        skill: list[str] = typer.Option([], "--skill", help="Repeatable skill id."),
        subagent: list[str] = typer.Option([], "--subagent", help="Repeatable subagent name."),
        tool: list[str] = typer.Option([], "--tool", help="Repeatable enabled tool id."),
        bootstrap_step: list[str] = typer.Option([], "--bootstrap-step", help="Repeatable bootstrap step."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Patch the remote Cloud bot profile setup."""

        fields = _profile_fields(
            instructions=instructions,
            model=model,
            skill=skill,
            subagent=subagent,
            tool=tool,
            bootstrap_step=bootstrap_step,
            channel=[],
        )
        try:
            payload = (
                update_remote_profile_config(
                    settings=get_settings(),
                    connection_name=connection,
                    fields=fields,
                )
                if fields
                else read_remote_profile_config(settings=get_settings(), connection_name=connection)
            )
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return
        _render_profile_payload(payload, json_output=json_output)

    profile_app = typer.Typer(help="Manage remote Cloud bot profiles.", no_args_is_help=True)

    @profile_app.command("add")
    def profile_add(
        profile_id: str = typer.Argument(..., help="Profile id to configure on the Cloud bot."),
        connection: str = typer.Option("default", "--cloud", help="Saved Cloud connection name."),
        instructions: str | None = typer.Option(None, "--instructions", help="Profile instructions."),
        model: str | None = typer.Option(None, "--model", help="Profile model id."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Add or update one remote profile entry in Cloud profile config metadata."""

        profile_fields: dict[str, str] = {}
        if instructions is not None:
            profile_fields["instructions"] = instructions
        if model is not None:
            profile_fields["model"] = model
        try:
            existing = read_remote_profile_config(settings=get_settings(), connection_name=connection)
            metadata = _dict_value(existing.get("metadata"))
            profiles = _dict_value(metadata.get("profiles"))
            current_profile = _dict_value(profiles.get(profile_id))
            fields: dict[str, object] = {
                "metadata": {
                    **metadata,
                    "profiles": {
                        **profiles,
                        profile_id: {**current_profile, **profile_fields},
                    },
                }
            }
            payload = update_remote_profile_config(
                settings=get_settings(),
                connection_name=connection,
                fields=fields,
            )
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return
        _render_profile_payload(payload, json_output=json_output)

    channel_app = typer.Typer(help="Manage remote Cloud bot channels.", no_args_is_help=True)

    @channel_app.command("add")
    def channel_add(
        kind: str = typer.Argument(..., help="Channel kind, for example telegram or chat."),
        connection: str = typer.Option("default", "--cloud", help="Saved Cloud connection name."),
        channel_id: str | None = typer.Option(None, "--id", help="Channel id."),
        enabled: bool = typer.Option(True, "--enabled/--disabled", help="Enable the channel."),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Add one remote channel descriptor to Cloud profile config."""

        channel_payload = {"kind": kind, "enabled": enabled}
        if channel_id:
            channel_payload["id"] = channel_id
        try:
            existing = read_remote_profile_config(settings=get_settings(), connection_name=connection)
            existing_channels = _list_value(existing.get("channels"))
            payload = update_remote_profile_config(
                settings=get_settings(),
                connection_name=connection,
                fields={"channels": [*existing_channels, channel_payload]},
            )
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return
        _render_profile_payload(payload, json_output=json_output)

    cloud_app.add_typer(profile_app, name="profile")
    cloud_app.add_typer(channel_app, name="channel")

    app.add_typer(cloud_app, name="cloud")


def _render_error(exc: CloudRemoteError, *, json_output: bool) -> None:
    """Render a Cloud remote error consistently.

    :param exc: Expected Cloud remote error.
    :param json_output: Whether the command is in machine-readable mode.
    :return: None.
    """

    if json_output:
        typer.echo(json.dumps({"ok": False, "error_code": exc.error_code, "reason": exc.reason}, ensure_ascii=True))
        raise typer.Exit(code=1)
    raise_usage_error(exc.reason)


def _run_lifecycle_command(*, connection: str, action: str, json_output: bool) -> None:
    """Run and render one Cloud lifecycle command.

    :param connection: Saved Cloud connection name.
    :param action: Lifecycle action.
    :param json_output: Whether to render machine-readable JSON.
    :return: None.
    """

    try:
        payload = run_remote_lifecycle_action(
            settings=get_settings(),
            connection_name=connection,
            action=action,
        )
    except CloudRemoteError as exc:
        _render_error(exc, json_output=json_output)
        return
    if json_output:
        typer.echo(json.dumps({"ok": True, "result": payload}, ensure_ascii=True))
        return
    typer.echo(f"cloud {payload.get('action', action)} accepted: {payload.get('state', 'ok')}")


def _attach_remote_chat_replies(*, connection: str, payload: dict[str, object]) -> dict[str, object]:
    """Attach recent assistant replies for the sent Cloud command.

    :param connection: Saved Cloud connection name.
    :param payload: Sent message payload.
    :return: Payload with an optional `replies` field.
    """

    command_id = str(payload.get("command_id") or "")
    if not command_id:
        return payload
    deadline = time.monotonic() + 60
    results: list[object] = []
    while time.monotonic() < deadline:
        messages = poll_remote_chat_messages(
            settings=get_settings(),
            connection_name=connection,
            command_id=command_id,
            limit=20,
        )
        raw_results = messages.get("results", [])
        results = raw_results if isinstance(raw_results, list) else []
        if any(isinstance(item, dict) and item.get("role") == "assistant" for item in results):
            break
        time.sleep(2)
    return {**payload, "replies": results}


def _render_chat_payload(payload: dict[str, object]) -> None:
    """Render a remote chat payload for humans.

    :param payload: Cloud API chat response.
    :return: None.
    """

    replies = payload.get("replies")
    if isinstance(replies, list):
        assistant = [
            item for item in replies if isinstance(item, dict) and item.get("role") == "assistant"
        ]
        if assistant:
            typer.echo(str(assistant[0].get("content") or ""))
            return
    typer.echo(f"message sent: {payload.get('status', 'sent')}")


def _render_profile_payload(payload: dict[str, object], *, json_output: bool) -> None:
    """Render a remote profile config payload.

    :param payload: Cloud API profile response.
    :param json_output: Whether to render machine-readable JSON.
    :return: None.
    """

    if json_output:
        typer.echo(json.dumps({"ok": True, "profile_config": payload}, ensure_ascii=True))
        return
    typer.echo("cloud profile config updated")
    typer.echo(f"model: {payload.get('model') or '(not set)'}")


def _profile_fields(
    *,
    instructions: str | None,
    model: str | None,
    skill: list[str],
    subagent: list[str],
    tool: list[str],
    bootstrap_step: list[str],
    channel: list[dict[str, object]],
) -> dict[str, object]:
    """Build a sparse remote profile config patch.

    :param instructions: Optional instructions text.
    :param model: Optional model id.
    :param skill: Skill ids.
    :param subagent: Subagent names.
    :param tool: Enabled tool ids.
    :param bootstrap_step: Bootstrap steps.
    :param channel: Channel descriptors.
    :return: Sparse profile config fields.
    """

    fields: dict[str, object] = {}
    if instructions is not None:
        fields["instructions"] = instructions
    if model is not None:
        fields["model"] = model
    if skill:
        fields["skills"] = skill
    if subagent:
        fields["subagents"] = subagent
    if tool:
        fields["enabled_tools"] = tool
    if bootstrap_step:
        fields["bootstrap_steps"] = bootstrap_step
    if channel:
        fields["channels"] = channel
    return fields


def _dict_value(value: Any) -> dict[str, Any]:
    """Return a string-key dict for JSON object values.

    :param value: Unknown JSON value.
    :return: Dict with string keys or an empty dict.
    """

    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_value(value: Any) -> list[Any]:
    """Return a list for JSON array values.

    :param value: Unknown JSON value.
    :return: List value or an empty list.
    """

    return value if isinstance(value, list) else []
