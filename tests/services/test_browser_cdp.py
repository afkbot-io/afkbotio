"""Tests for shared browser CDP endpoint helpers."""

from __future__ import annotations

import pytest

from afkbot.services.browser_cdp import (
    browser_cdp_host_port,
    browser_cdp_url_is_local,
    normalize_browser_cdp_url,
)


def test_normalize_browser_cdp_url_supports_host_port_shorthand() -> None:
    """CDP shorthand should normalize to an HTTP endpoint URL."""

    # Arrange
    raw_url = "127.0.0.1:9222"

    # Act
    normalized = normalize_browser_cdp_url(raw_url)

    # Assert
    assert normalized == "http://127.0.0.1:9222"
    assert browser_cdp_host_port(normalized) == ("127.0.0.1", 9222)
    assert browser_cdp_url_is_local(normalized) is True


def test_normalize_browser_cdp_url_supports_auth_shorthand() -> None:
    """CDP shorthand with auth should normalize into one safe URL."""

    # Arrange
    raw_url = "lightpanda.local:9222:user:pass"

    # Act
    normalized = normalize_browser_cdp_url(raw_url)

    # Assert
    assert normalized == "http://user:pass@lightpanda.local:9222"
    assert browser_cdp_url_is_local(normalized) is False


def test_normalize_browser_cdp_url_rejects_non_cdp_schemes() -> None:
    """CDP normalization should fail closed for unsupported URL schemes."""

    # Arrange
    raw_url = "socks5://127.0.0.1:9222"

    # Act
    with pytest.raises(ValueError) as exc_info:
        normalize_browser_cdp_url(raw_url)

    # Assert
    assert "http, https, ws, or wss" in str(exc_info.value)


def test_browser_cdp_url_is_local_rejects_bind_all_host() -> None:
    """0.0.0.0 must not be treated as a local-only CDP endpoint."""

    # Arrange
    raw_url = "http://0.0.0.0:9222"

    # Act
    is_local = browser_cdp_url_is_local(raw_url)

    # Assert
    assert is_local is False
