"""Remote AFKBOT Cloud connection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from afkbot.services.setup.runtime_store import (
    read_runtime_config,
    read_runtime_secrets,
    write_runtime_config,
    write_runtime_secrets,
)
from afkbot.settings import Settings

DEFAULT_CLOUD_API_URL = "https://cloud.afkbot.io/api/v1"
REMOTE_CONNECT_SCOPE = "remote_connect"
TOKEN_SECRET_PREFIX = "cloud_remote_token:"
_CONNECTION_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class CloudRemoteError(RuntimeError):
    """Expected remote Cloud connection failure."""

    def __init__(self, reason: str, *, error_code: str = "cloud_remote_error") -> None:
        """Create a typed Cloud remote error.

        :param reason: Operator-safe failure reason.
        :param error_code: Stable error code for JSON CLI output.
        :return: None.
        """

        super().__init__(reason)
        self.reason = reason
        self.error_code = error_code


@dataclass(frozen=True)
class RemoteBotConnection:
    """Persisted remote bot connection metadata."""

    name: str
    api_url: str
    public_url: str
    bot_id: str
    bot_name: str
    organization_id: str
    status: str
    scopes: list[str]
    profile_config: dict[str, Any]
    connected_at: str


def resolve_cloud_api_url(api_url: str | None = None, *, public_url: str | None = None) -> str:
    """Resolve and normalize the Cloud API base URL.

    :param api_url: Explicit Cloud API base URL from CLI.
    :param public_url: Optional public bot URL used to infer the dashboard API URL.
    :return: Normalized URL without trailing slash.
    """

    value = (
        api_url
        or os.getenv("AFKBOT_CLOUD_API_URL")
        or infer_cloud_api_url_from_public_url(public_url or "")
        or DEFAULT_CLOUD_API_URL
    ).strip()
    if not value:
        raise CloudRemoteError("Cloud API URL is required.", error_code="cloud_api_url_required")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CloudRemoteError("Cloud API URL must be a valid http or https URL.", error_code="cloud_api_url_invalid")
    if parsed.scheme == "http" and not _is_local_cloud_api_host(parsed.hostname or ""):
        raise CloudRemoteError(
            "Cloud API URL must use HTTPS outside local development.",
            error_code="cloud_api_url_insecure",
        )
    return value.rstrip("/")


def infer_cloud_api_url_from_public_url(public_url: str) -> str:
    """Infer the Cloud API URL from a public workspace or bot URL.

    :param public_url: Public Cloud URL such as `https://abc.cloud.afkbot.io/bot/xyz`.
    :return: Inferred API base URL or an empty string when it cannot be inferred.
    """

    parsed = urlparse(public_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = parsed.hostname.lower().strip(".")
    labels = hostname.split(".")
    if len(labels) >= 4 and labels[1] == "cloud":
        api_host = ".".join(labels[1:])
    elif hostname.startswith("cloud."):
        api_host = hostname
    else:
        return ""
    netloc = api_host
    if parsed.port:
        netloc = f"{api_host}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, "/api/v1", "", "", ""))


def verify_remote_bot_connection(*, api_url: str, public_url: str, token: str) -> dict[str, Any]:
    """Verify a Cloud bot token against the control-plane API.

    :param api_url: Normalized Cloud API base URL.
    :param public_url: Public bot URL copied from the Cloud cabinet.
    :param token: Plaintext remote connection token.
    :return: Verified response payload from the Cloud API.
    """

    if not public_url.strip():
        raise CloudRemoteError("Bot public URL is required.", error_code="public_url_required")
    if not token.strip():
        raise CloudRemoteError("Bot token is required.", error_code="token_required")

    body = json.dumps({"public_url": public_url.strip(), "token": token.strip()}).encode("utf-8")
    request = Request(
        f"{api_url}/bot-access/verify",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - URL is operator supplied and validated.
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        reason = _extract_http_error_reason(exc)
        raise CloudRemoteError(reason, error_code="cloud_token_rejected") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CloudRemoteError("Cloud API is unavailable.", error_code="cloud_api_unavailable") from exc
    except json.JSONDecodeError as exc:
        raise CloudRemoteError("Cloud API returned an invalid response.", error_code="cloud_api_invalid_response") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("bot"), dict):
        raise CloudRemoteError("Cloud API returned an invalid response.", error_code="cloud_api_invalid_response")
    return payload


def save_remote_bot_connection(
    *,
    settings: Settings,
    name: str,
    api_url: str,
    public_url: str,
    token: str,
    verification_payload: dict[str, Any],
) -> RemoteBotConnection:
    """Persist verified remote connection metadata and encrypted token.

    :param settings: Active AFKBOT settings.
    :param name: Local connection name.
    :param api_url: Cloud API base URL.
    :param public_url: Public bot URL.
    :param token: Plaintext token to store in runtime secrets.
    :param verification_payload: Verified Cloud API payload.
    :return: Persisted remote connection metadata.
    """

    connection_name = normalize_remote_connection_name(name)
    bot_payload = verification_payload.get("bot")
    token_payload = verification_payload.get("token")
    if not isinstance(bot_payload, dict) or not isinstance(token_payload, dict):
        raise CloudRemoteError("Cloud API returned an invalid response.", error_code="cloud_api_invalid_response")
    scopes = token_payload.get("scopes") if isinstance(token_payload.get("scopes"), list) else [REMOTE_CONNECT_SCOPE]
    profile_config = verification_payload.get("profile_config")
    if not isinstance(profile_config, dict):
        profile_config = {}
    connection = RemoteBotConnection(
        name=connection_name,
        api_url=api_url,
        public_url=str(bot_payload.get("public_url") or public_url),
        bot_id=str(bot_payload.get("id") or ""),
        bot_name=str(bot_payload.get("name") or ""),
        organization_id=str(bot_payload.get("organization_id") or ""),
        status=str(bot_payload.get("status") or ""),
        scopes=[str(scope) for scope in scopes],
        profile_config=profile_config,
        connected_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
    )
    config = read_runtime_config(settings)
    existing = config.get("cloud_remote_connections")
    connections = existing if isinstance(existing, dict) else {}
    connections[connection_name] = {
        "api_url": connection.api_url,
        "public_url": connection.public_url,
        "bot_id": connection.bot_id,
        "bot_name": connection.bot_name,
        "organization_id": connection.organization_id,
        "status": connection.status,
        "scopes": connection.scopes,
        "profile_config": connection.profile_config,
        "connected_at": connection.connected_at,
    }
    config["cloud_remote_connections"] = connections
    write_runtime_config(settings, config=config)

    secrets = read_runtime_secrets(settings)
    secrets[f"{TOKEN_SECRET_PREFIX}{connection_name}"] = token
    write_runtime_secrets(settings, secrets=secrets)
    return connection


def list_remote_bot_connections(*, settings: Settings) -> list[RemoteBotConnection]:
    """List persisted remote bot connection metadata.

    :param settings: Active AFKBOT settings.
    :return: Sorted remote connection metadata.
    """

    config = read_runtime_config(settings)
    raw_connections = config.get("cloud_remote_connections")
    if not isinstance(raw_connections, dict):
        return []
    connections: list[RemoteBotConnection] = []
    for name, payload in raw_connections.items():
        if not isinstance(payload, dict):
            continue
        connections.append(
            RemoteBotConnection(
                name=str(name),
                api_url=str(payload.get("api_url") or ""),
                public_url=str(payload.get("public_url") or ""),
                bot_id=str(payload.get("bot_id") or ""),
                bot_name=str(payload.get("bot_name") or ""),
                organization_id=str(payload.get("organization_id") or ""),
                status=str(payload.get("status") or ""),
                scopes=[str(scope) for scope in payload.get("scopes", []) if isinstance(scope, str)],
                profile_config=payload.get("profile_config") if isinstance(payload.get("profile_config"), dict) else {},
                connected_at=str(payload.get("connected_at") or ""),
            )
        )
    return sorted(connections, key=lambda item: item.name)


def _is_local_cloud_api_host(hostname: str) -> bool:
    """Return whether a host is safe for plain HTTP local development.

    :param hostname: Parsed URL host.
    :return: True for localhost-style Cloud development hosts.
    """

    host = hostname.lower().strip(".")
    return host in {"localhost", "127.0.0.1", "::1", "cloud.afkbot.local"} or host.endswith(".localhost")


def normalize_remote_connection_name(name: str) -> str:
    """Normalize and validate a local remote connection name.

    :param name: User-provided local name.
    :return: Valid local name.
    """

    normalized = (name or "default").strip()
    if not _CONNECTION_NAME_RE.match(normalized):
        raise CloudRemoteError(
            "Connection name must use letters, numbers, dots, dashes, or underscores.",
            error_code="connection_name_invalid",
        )
    return normalized


def _extract_http_error_reason(exc: HTTPError) -> str:
    """Read a safe error message from an HTTP error response.

    :param exc: HTTP error raised by urllib.
    :return: Operator-safe error message.
    """

    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "Cloud rejected the token."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return "Cloud rejected the token."
