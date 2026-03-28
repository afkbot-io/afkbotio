"""Pinned urllib opener helpers for SSRF-safe outbound HTTP fetches."""

from __future__ import annotations

import http.client
import socket
import ssl
from typing import Any, Final, cast
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    Request,
    build_opener,
)

_DEFAULT_TIMEOUT: Final[object] = cast(object, getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT"))


def build_pinned_opener(*, url: str, resolved_addresses: tuple[str, ...]) -> OpenerDirector:
    """Build urllib opener pinned to one pre-resolved IP address."""

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    host = str(parsed.hostname or "").strip()
    if not host:
        raise ValueError("URL host is required")
    if not resolved_addresses:
        raise ValueError("Resolved address set is empty")

    explicit_port = parsed.port
    port = int(explicit_port) if explicit_port is not None else (443 if scheme == "https" else 80)
    pinned_host = resolved_addresses[0]
    logical_host = parsed.netloc or host
    if scheme == "http":
        return build_opener(
            _NoRedirect(),
            _PinnedHTTPHandler(
                logical_host=logical_host,
                pinned_host=pinned_host,
                pinned_port=port,
            ),
        )
    return build_opener(
        _NoRedirect(),
        _PinnedHTTPSHandler(
            logical_host=logical_host,
            pinned_host=pinned_host,
            pinned_port=port,
        ),
    )


class _NoRedirect(HTTPRedirectHandler):
    """Block automatic redirect follow-up for stricter policy boundaries."""

    def redirect_request(
        self,
        req: object,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        _ = req, fp, code, msg, headers, newurl
        return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that dials one pre-resolved IP while preserving logical host."""

    source_address: tuple[str, int] | None

    def __init__(
        self,
        host: str,
        *,
        pinned_host: str,
        pinned_port: int,
        timeout: float | object = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(host=host, timeout=cast(Any, timeout))
        self._pinned_host = pinned_host
        self._pinned_port = int(pinned_port)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_host, self._pinned_port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials one pre-resolved IP and keeps SNI on logical host."""

    source_address: tuple[str, int] | None
    _tunnel_host: str | None
    _context: ssl.SSLContext

    def __init__(
        self,
        host: str,
        *,
        pinned_host: str,
        pinned_port: int,
        timeout: float | object = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(host=host, timeout=cast(Any, timeout))
        self._pinned_host = pinned_host
        self._pinned_port = int(pinned_port)

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_host, self._pinned_port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = raw_socket
            cast(Any, self)._tunnel()
            raw_socket = self.sock
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class _PinnedHTTPHandler(HTTPHandler):
    """urllib handler that builds pinned HTTP connections."""

    def __init__(self, *, logical_host: str, pinned_host: str, pinned_port: int) -> None:
        super().__init__()
        self._logical_host = logical_host
        self._pinned_host = pinned_host
        self._pinned_port = int(pinned_port)

    def http_open(self, req: Request) -> http.client.HTTPResponse:
        return self.do_open(self._open, req)

    def _open(self, host: str, **kwargs: Any) -> _PinnedHTTPConnection:
        _ = host
        timeout = cast(float | object, kwargs.get("timeout", _DEFAULT_TIMEOUT))
        return _PinnedHTTPConnection(
            host=self._logical_host,
            pinned_host=self._pinned_host,
            pinned_port=self._pinned_port,
            timeout=timeout,
        )


class _PinnedHTTPSHandler(HTTPSHandler):
    """urllib handler that builds pinned HTTPS connections."""

    def __init__(self, *, logical_host: str, pinned_host: str, pinned_port: int) -> None:
        super().__init__()
        self._logical_host = logical_host
        self._pinned_host = pinned_host
        self._pinned_port = int(pinned_port)

    def https_open(self, req: Request) -> http.client.HTTPResponse:
        return self.do_open(self._open, req)

    def _open(self, host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
        _ = host
        timeout = cast(float | object, kwargs.get("timeout", _DEFAULT_TIMEOUT))
        return _PinnedHTTPSConnection(
            host=self._logical_host,
            pinned_host=self._pinned_host,
            pinned_port=self._pinned_port,
            timeout=timeout,
        )
