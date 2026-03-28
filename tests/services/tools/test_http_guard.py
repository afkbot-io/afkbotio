"""Unit tests for shared HTTP network guard helpers."""

from __future__ import annotations

import pytest

from afkbot.services.tools.network.http_guard import resolve_public_network_addresses


def test_resolve_public_network_addresses_rejects_localhost() -> None:
    """localhost targets must be rejected before any network call."""

    with pytest.raises(ValueError, match="must not target localhost"):
        _ = resolve_public_network_addresses("https://localhost/path")


def test_resolve_public_network_addresses_rejects_non_public_ip() -> None:
    """Resolver results that point to non-global IPs must be rejected."""

    with pytest.raises(ValueError, match="non-public network address"):
        _ = resolve_public_network_addresses(
            "https://example.com/path",
            resolver=lambda host, port: ("10.0.0.2",),
        )


def test_resolve_public_network_addresses_rejects_invalid_ip() -> None:
    """Resolver results must be valid IP addresses."""

    with pytest.raises(ValueError, match="not a valid IP address"):
        _ = resolve_public_network_addresses(
            "https://example.com/path",
            resolver=lambda host, port: ("not-an-ip",),
        )


def test_resolve_public_network_addresses_allows_public_ip() -> None:
    """Global IP addresses must pass and be returned unchanged."""

    addresses = resolve_public_network_addresses(
        "https://example.com/path",
        resolver=lambda host, port: ("93.184.216.34",),
    )
    assert addresses == ("93.184.216.34",)
