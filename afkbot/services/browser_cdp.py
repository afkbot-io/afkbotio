"""Shared helpers for CDP endpoint normalization and host inspection."""

from __future__ import annotations

from urllib.parse import quote, urlparse


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_CDP_SCHEMES = {"http", "https", "ws", "wss"}


def normalize_browser_cdp_url(raw_url: str) -> str:
    """Normalize one CDP endpoint URL, supporting shorthand host/port forms."""

    candidate = raw_url.strip()
    if not candidate:
        return ""
    if "://" in candidate:
        parsed = urlparse(candidate)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_CDP_SCHEMES or not parsed.hostname:
            raise ValueError(
                "Browser CDP URL must use http, https, ws, or wss and include a hostname."
            )
        return candidate

    parts = candidate.split(":")
    if len(parts) == 2:
        host, port = parts
        return _compose_shorthand_url(host=host, port=port)
    if len(parts) == 4:
        host, port, username, password = parts
        return _compose_shorthand_url(
            host=host,
            port=port,
            username=username,
            password=password,
        )
    raise ValueError(
        "Browser CDP URL shorthand must be host:port or host:port:user:pass, "
        "or a full http(s)/ws(s) URL."
    )


def browser_cdp_host_port(raw_url: str) -> tuple[str, int]:
    """Return normalized host and port for one CDP endpoint URL."""

    normalized = normalize_browser_cdp_url(raw_url)
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, parsed.port
    if parsed.scheme in {"https", "wss"}:
        return host, 443
    return host, 80


def browser_cdp_url_is_local(raw_url: str) -> bool:
    """Return whether one CDP endpoint targets a local bind address."""

    host, _ = browser_cdp_host_port(raw_url)
    return host.lower() in _LOOPBACK_HOSTS


def _compose_shorthand_url(
    *,
    host: str,
    port: str,
    username: str | None = None,
    password: str | None = None,
) -> str:
    cleaned_host = host.strip()
    cleaned_port = port.strip()
    if not cleaned_host or not cleaned_port.isdigit():
        raise ValueError(
            "Browser CDP URL shorthand must include a hostname and numeric port."
        )
    if username is None and password is None:
        return f"http://{cleaned_host}:{cleaned_port}"
    cleaned_username = (username or "").strip()
    cleaned_password = (password or "").strip()
    if not cleaned_username or not cleaned_password:
        raise ValueError(
            "Browser CDP URL shorthand auth must be host:port:user:pass."
        )
    auth = f"{quote(cleaned_username, safe='')}:{quote(cleaned_password, safe='')}"
    return f"http://{auth}@{cleaned_host}:{cleaned_port}"
