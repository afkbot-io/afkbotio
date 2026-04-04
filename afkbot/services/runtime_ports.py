"""Helpers for default runtime port selection and availability checks."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
import os
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afkbot.settings import Settings

DEFAULT_EXOTIC_RUNTIME_PORT = 46339
_DEFAULT_API_PORT_OFFSET = 1
_PORT_SCAN_ATTEMPTS = 64


def resolve_default_runtime_port(
    *,
    settings: Settings,
    host: str,
    runtime_config: Mapping[str, object] | None = None,
) -> int:
    """Return the implicit runtime port used when the operator did not set one."""

    runtime_config = runtime_config or {}
    if _has_explicit_runtime_port_env():
        return settings.runtime_port
    persisted_port = _coerce_port(runtime_config.get("runtime_port"))
    if persisted_port is not None:
        return persisted_port
    if settings.runtime_port != DEFAULT_EXOTIC_RUNTIME_PORT:
        return settings.runtime_port
    return find_available_runtime_port(host=host, preferred_port=DEFAULT_EXOTIC_RUNTIME_PORT)


def find_available_runtime_port(
    *,
    host: str,
    preferred_port: int = DEFAULT_EXOTIC_RUNTIME_PORT,
    attempts: int = _PORT_SCAN_ATTEMPTS,
) -> int:
    """Return one runtime port whose API sibling port also looks available."""

    candidates = [preferred_port]
    for offset in range(1, max(1, attempts)):
        candidate = preferred_port + (offset * 2)
        if candidate + _DEFAULT_API_PORT_OFFSET > 65535:
            break
        candidates.append(candidate)
    for candidate in candidates:
        if is_runtime_port_pair_available(host=host, runtime_port=candidate):
            return candidate
    return preferred_port


def is_runtime_port_pair_available(*, host: str, runtime_port: int) -> bool:
    """Return whether runtime/api sibling ports both appear bindable locally."""

    api_port = runtime_port + _DEFAULT_API_PORT_OFFSET
    return _is_tcp_port_available(host=host, port=runtime_port) and _is_tcp_port_available(
        host=host,
        port=api_port,
    )


def _coerce_port(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            port = int(normalized)
        except ValueError:
            return None
    else:
        return None
    if not (1 <= port <= 65535):
        return None
    return port


def _has_explicit_runtime_port_env() -> bool:
    raw_value = os.getenv("AFKBOT_RUNTIME_PORT")
    return raw_value is not None and bool(raw_value.strip())


def _is_tcp_port_available(*, host: str, port: int) -> bool:
    normalized_host = host.strip() or "127.0.0.1"
    try:
        infos = socket.getaddrinfo(
            normalized_host,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except OSError:
        return False
    for family, socktype, proto, _, sockaddr in infos:
        try:
            with closing(socket.socket(family, socktype, proto)) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(sockaddr)
        except OSError:
            continue
        return True
    return False
