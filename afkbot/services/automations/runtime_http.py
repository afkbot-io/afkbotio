"""HTTP parsing and response helpers for automation runtime ingress."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Final
from afkbot.services.automations.contracts import WEBHOOK_INGRESS_PATH
from afkbot.services.automations.runtime_service import coerce_webhook_payload_mapping

STATUS_TEXT: Final[dict[int, str]] = {
    200: "OK",
    202: "Accepted",
    408: "Request Timeout",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    413: "Payload Too Large",
    429: "Too Many Requests",
    503: "Service Unavailable",
}

WEBHOOK_TOKEN_HEADER: Final[str] = "x-afk-webhook-token"


@dataclass(frozen=True)
class HttpRequest:
    """One parsed HTTP request consumed by runtime daemon routing."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class HttpReadError:
    """Structured read/parse failure returned by request parser."""

    status_code: int
    payload: Mapping[str, object]


def match_webhook_path(path: str) -> tuple[bool, str | None]:
    """Return whether a path targets webhook ingress."""

    if path != WEBHOOK_INGRESS_PATH:
        return False, None
    return True, None


def extract_webhook_token(headers: Mapping[str, str]) -> str | None:
    """Extract webhook token from headers with normalization."""

    token = (headers.get(WEBHOOK_TOKEN_HEADER) or "").strip()
    return token or None


async def read_request(
    reader: asyncio.StreamReader,
    *,
    read_timeout_sec: float,
    max_header_bytes: int,
    max_body_bytes: int,
) -> HttpRequest | HttpReadError:
    """Read one HTTP/1.1 request from stream or return structured parse error."""

    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=read_timeout_sec)
    except asyncio.LimitOverrunError:
        return HttpReadError(
            status_code=413,
            payload={
                "ok": False,
                "error_code": "header_too_large",
                "reason": "Request header section is too large",
            },
        )
    except asyncio.TimeoutError:
        return HttpReadError(
            status_code=408,
            payload={
                "ok": False,
                "error_code": "request_timeout",
                "reason": "Timed out while reading request",
            },
        )
    except asyncio.IncompleteReadError:
        return HttpReadError(
            status_code=400,
            payload={
                "ok": False,
                "error_code": "invalid_http_request",
                "reason": "Invalid HTTP request",
            },
        )
    if len(head) > max_header_bytes:
        return HttpReadError(
            status_code=413,
            payload={
                "ok": False,
                "error_code": "header_too_large",
                "reason": "Request header section is too large",
            },
        )
    try:
        request_line, *raw_headers = head.decode("latin-1").split("\r\n")
    except UnicodeDecodeError:
        return HttpReadError(
            status_code=400,
            payload={
                "ok": False,
                "error_code": "invalid_http_request",
                "reason": "Invalid HTTP request",
            },
        )
    if not request_line:
        return HttpReadError(
            status_code=400,
            payload={
                "ok": False,
                "error_code": "invalid_http_request",
                "reason": "Invalid HTTP request",
            },
        )
    parts = request_line.split(" ")
    if len(parts) < 3:
        return HttpReadError(
            status_code=400,
            payload={
                "ok": False,
                "error_code": "invalid_http_request",
                "reason": "Invalid HTTP request",
            },
        )
    method = parts[0].upper()
    raw_path = parts[1]
    path = raw_path.split("?", maxsplit=1)[0]
    headers: dict[str, str] = {}
    for header_line in raw_headers:
        if not header_line:
            continue
        if ":" not in header_line:
            return HttpReadError(
                status_code=400,
                payload={
                    "ok": False,
                    "error_code": "invalid_http_request",
                    "reason": "Invalid HTTP request",
                },
            )
        name, value = header_line.split(":", maxsplit=1)
        headers[name.strip().lower()] = value.strip()
    transfer_encoding = headers.get("transfer-encoding", "").strip().lower()
    if transfer_encoding and transfer_encoding != "identity":
        return HttpReadError(
            status_code=400,
            payload={
                "ok": False,
                "error_code": "unsupported_transfer_encoding",
                "reason": "Only identity transfer encoding is supported",
            },
        )
    content_length = 0
    raw_content_length = headers.get("content-length")
    if raw_content_length:
        try:
            content_length = int(raw_content_length)
        except ValueError:
            return HttpReadError(
                status_code=400,
                payload={
                    "ok": False,
                    "error_code": "invalid_http_request",
                    "reason": "Invalid HTTP request",
                },
            )
        if content_length < 0:
            return HttpReadError(
                status_code=400,
                payload={
                    "ok": False,
                    "error_code": "invalid_http_request",
                    "reason": "Invalid HTTP request",
                },
            )
    if content_length > max_body_bytes:
        return HttpReadError(
            status_code=413,
            payload={
                "ok": False,
                "error_code": "payload_too_large",
                "reason": "Payload exceeds configured size limit",
            },
        )
    body = b""
    if content_length > 0:
        try:
            body = await asyncio.wait_for(
                reader.readexactly(content_length),
                timeout=read_timeout_sec,
            )
        except asyncio.TimeoutError:
            return HttpReadError(
                status_code=408,
                payload={
                    "ok": False,
                    "error_code": "request_timeout",
                    "reason": "Timed out while reading request body",
                },
            )
        except asyncio.IncompleteReadError:
            return HttpReadError(
                status_code=400,
                payload={
                    "ok": False,
                    "error_code": "invalid_http_request",
                    "reason": "Invalid HTTP request",
                },
            )
    return HttpRequest(method=method, path=path, headers=headers, body=body)


async def write_json_response(
    writer: asyncio.StreamWriter,
    *,
    status_code: int,
    payload: Mapping[str, object],
) -> None:
    """Write one JSON response and close the underlying stream."""

    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    status_text = STATUS_TEXT.get(status_code, "Unknown")
    head = (
        f"HTTP/1.1 {status_code} {status_text}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    writer.write(head + body)
    with suppress(ConnectionError, BrokenPipeError):
        await writer.drain()
    writer.close()
    with suppress(ConnectionError, BrokenPipeError):
        await writer.wait_closed()


def parse_webhook_payload(body: bytes) -> Mapping[str, object]:
    """Parse one webhook JSON body into the mapping shape accepted by runtime service."""

    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload must be a valid JSON object") from exc
    return coerce_webhook_payload_mapping(parsed)
