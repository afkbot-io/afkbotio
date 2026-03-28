"""HTTP/HTTPS network target safety helpers shared by tool plugins."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse


HostResolver = Callable[[str, int], tuple[str, ...]]


def ensure_public_network_target(
    url: str,
    *,
    resolver: HostResolver | None = None,
) -> None:
    """Validate that URL target resolves only to public IP addresses."""

    _ = resolve_public_network_addresses(url, resolver=resolver)


def resolve_public_network_addresses(
    parsed_url: object,
    *,
    resolver: HostResolver | None = None,
) -> tuple[str, ...]:
    """Resolve URL host and reject localhost/private/non-global targets."""

    parsed = parsed_url if hasattr(parsed_url, "hostname") else urlparse(str(parsed_url))
    host = str(getattr(parsed, "hostname", "") or "").strip().lower()
    if not host:
        raise ValueError("URL host is required")
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("URL host must not target localhost")

    explicit_port = getattr(parsed, "port", None)
    scheme = str(getattr(parsed, "scheme", "") or "").lower()
    port = int(explicit_port) if explicit_port is not None else (443 if scheme == "https" else 80)

    host_resolver = resolver or resolve_host_addresses
    addresses = host_resolver(host, port)
    for address in addresses:
        sanitized = address.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(sanitized)
        except ValueError as exc:
            raise ValueError(f"Resolved host is not a valid IP address: {address}") from exc
        if not ip.is_global:
            raise ValueError(
                "URL host resolves to a non-public network address, request is denied"
            )
    return addresses


def resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    """Resolve one host to deterministic unique address tuple."""

    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve URL host: {host}") from exc

    addresses: list[str] = []
    for row in info:
        sockaddr = row[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            continue
        raw = str(sockaddr[0]).strip()
        if raw:
            addresses.append(raw)
    if not addresses:
        raise ValueError(f"Unable to resolve URL host: {host}")
    return tuple(dict.fromkeys(addresses))
