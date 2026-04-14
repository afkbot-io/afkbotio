"""Helpers for default runtime port selection and availability checks."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from contextlib import closing
import json
import os
import socket
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

if TYPE_CHECKING:
    from afkbot.settings import Settings

DEFAULT_EXOTIC_RUNTIME_PORT = 46339
_DEFAULT_API_PORT_OFFSET = 1
_PORT_SCAN_ATTEMPTS = 64


@dataclass(frozen=True, slots=True)
class RuntimeEndpointProbe:
    """One local health probe outcome."""

    ok: bool
    url: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStackProbe:
    """Combined health result for the runtime daemon and API sibling port."""

    runtime: RuntimeEndpointProbe
    api: RuntimeEndpointProbe

    @property
    def running(self) -> bool:
        return self.runtime.ok and self.api.ok

    @property
    def conflict(self) -> bool:
        return not self.running and (self.runtime.reason is not None or self.api.reason is not None)


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


def probe_runtime_stack(
    *,
    host: str,
    runtime_port: int,
    api_port: int | None = None,
    timeout_sec: float = 1.0,
) -> RuntimeStackProbe:
    """Probe AFKBOT-specific health endpoints on the runtime and API sibling ports."""

    resolved_api_port = api_port if api_port is not None else runtime_port + _DEFAULT_API_PORT_OFFSET
    probe_host = _normalize_probe_host(host)
    runtime = _probe_json_health_endpoint(
        url=f"http://{probe_host}:{runtime_port}/healthz",
        validator=lambda payload: payload.get("ok") is True
        and str(payload.get("service") or "").strip() == "afkbot-runtime",
        timeout_sec=timeout_sec,
    )
    api = _probe_json_health_endpoint(
        url=f"http://{probe_host}:{resolved_api_port}/healthz",
        validator=lambda payload: str(payload.get("status") or "").strip().lower() == "ok"
        and str(payload.get("service") or "").strip() == "afkbot-api",
        timeout_sec=timeout_sec,
    )
    return RuntimeStackProbe(runtime=runtime, api=api)


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


def _probe_json_health_endpoint(
    *,
    url: str,
    validator: Callable[[dict[str, object]], bool],
    timeout_sec: float,
) -> RuntimeEndpointProbe:
    try:
        with urlopen(url, timeout=max(timeout_sec, 0.1)) as response:
            raw_body = response.read().decode("utf-8")
    except (OSError, URLError) as exc:
        return RuntimeEndpointProbe(ok=False, url=url, reason=str(exc))
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return RuntimeEndpointProbe(ok=False, url=url, reason="invalid json payload")
    if not isinstance(payload, dict):
        return RuntimeEndpointProbe(ok=False, url=url, reason="invalid json payload")
    if not validator(payload):
        return RuntimeEndpointProbe(ok=False, url=url, reason="unexpected health payload")
    return RuntimeEndpointProbe(ok=True, url=url)


def _normalize_probe_host(host: str) -> str:
    normalized = host.strip() or "127.0.0.1"
    if normalized in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return normalized


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
