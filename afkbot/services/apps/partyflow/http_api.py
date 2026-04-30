"""Low-level PartyFlow Bot REST API HTTP helpers."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


class PartyFlowApiError(RuntimeError):
    """Structured PartyFlow Bot REST API failure."""

    def __init__(
        self,
        *,
        status_code: int | None,
        error_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.error_code = error_code
        self.reason = reason
        self.metadata = {} if metadata is None else metadata


async def _get_me(*, base_url: str, token: str, timeout_sec: int) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _request_json_sync,
            base_url=base_url,
            token=token,
            method="GET",
            path="/api/v1/bot/me",
            body=None,
            query=None,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _join_conversation(
    *,
    base_url: str,
    token: str,
    conversation_id: str,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _request_json_sync,
            base_url=base_url,
            token=token,
            method="POST",
            path=f"/api/v1/bot/conversations/{conversation_id}/join",
            body=None,
            query=None,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _get_messages(
    *,
    base_url: str,
    token: str,
    conversation_id: str,
    limit: int,
    before_msg_index: int | None,
    after_msg_index: int | None,
    around_msg_index: int | None,
    updated_since: str | None,
    thread_id: str | None,
    timeout_sec: int,
) -> dict[str, object]:
    query: dict[str, object] = {"limit": limit}
    if before_msg_index is not None:
        query["before_msg_index"] = before_msg_index
    if after_msg_index is not None:
        query["after_msg_index"] = after_msg_index
    if around_msg_index is not None:
        query["around_msg_index"] = around_msg_index
    if updated_since is not None:
        query["updated_since"] = updated_since
    if thread_id is not None:
        query["thread_id"] = thread_id
    return await asyncio.wait_for(
        asyncio.to_thread(
            _request_json_sync,
            base_url=base_url,
            token=token,
            method="GET",
            path=f"/api/v1/channels/{conversation_id}/messages",
            body=None,
            query=query,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _send_message(
    *,
    base_url: str,
    token: str,
    conversation_id: str,
    content: str,
    thread_id: str | None,
    display_name: str | None,
    display_avatar_url: str | None,
    metadata_json: str | None,
    timeout_sec: int,
) -> dict[str, object]:
    body: dict[str, object] = {
        "conversation_id": conversation_id,
        "content": content,
    }
    if thread_id is not None:
        body["thread_id"] = thread_id
    if display_name is not None:
        body["display_name"] = display_name
    if display_avatar_url is not None:
        body["display_avatar_url"] = display_avatar_url
    if metadata_json is not None:
        body["metadata_json"] = metadata_json
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _request_json_sync,
                base_url=base_url,
                token=token,
                method="POST",
                path="/api/v1/bot/messages",
                body=body,
                query=None,
                timeout_sec=timeout_sec,
            ),
            timeout=float(timeout_sec),
        )
    except PartyFlowApiError as exc:
        if exc.status_code != 403:
            raise
    await _join_conversation(
        base_url=base_url,
        token=token,
        conversation_id=conversation_id,
        timeout_sec=timeout_sec,
    )
    return await asyncio.wait_for(
        asyncio.to_thread(
            _request_json_sync,
            base_url=base_url,
            token=token,
            method="POST",
            path="/api/v1/bot/messages",
            body=body,
            query=None,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


def _request_json_sync(
    *,
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, object] | None,
    query: dict[str, object] | None,
    timeout_sec: int,
) -> dict[str, object]:
    normalized_base_url = base_url.strip().rstrip("/")
    normalized_token = token.strip()
    if not normalized_base_url:
        raise ValueError("PartyFlow base_url is empty")
    if not normalized_token:
        raise ValueError("PartyFlow token is empty")
    request_body: bytes | None = None
    headers = {
        "Authorization": f"Bearer {normalized_token}",
    }
    if body is not None:
        request_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    url = urljoin(f"{normalized_base_url}/", path.lstrip("/"))
    if query:
        filtered_query = {key: value for key, value in query.items() if value is not None}
        if filtered_query:
            url = f"{url}?{urlencode(filtered_query)}"
    request = Request(
        url=url,
        data=request_body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            raw = response.read()
    except HTTPError as exc:
        raise _http_error(exc) from exc
    except URLError as exc:
        raise PartyFlowApiError(
            status_code=None,
            error_code="app_run_failed",
            reason=f"PartyFlow request failed: {exc.reason}",
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PartyFlowApiError(
            status_code=None,
            error_code="app_run_failed",
            reason="PartyFlow response was not valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise PartyFlowApiError(
            status_code=None,
            error_code="app_run_failed",
            reason="PartyFlow response payload must be an object",
        )
    return {str(key): value for key, value in payload.items()}


def _http_error(exc: HTTPError) -> PartyFlowApiError:
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    try:
        parsed_payload: object = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed_payload = {}
    metadata: dict[str, object] = {"http_status": exc.code}
    retry_after_sec = _parse_retry_after(exc.headers.get("Retry-After"))
    if retry_after_sec is not None:
        metadata["retry_after_sec"] = retry_after_sec
    if isinstance(parsed_payload, dict):
        metadata["response"] = {str(key): value for key, value in parsed_payload.items()}
    reason = f"PartyFlow HTTP {exc.code}: {exc.reason}"
    error_code = "app_run_failed"
    if exc.code == 401:
        error_code = "partyflow_unauthorized"
    elif exc.code == 403:
        error_code = "partyflow_bot_not_in_conversation"
    elif exc.code == 429:
        error_code = "partyflow_rate_limited"
    elif exc.code in {500, 503}:
        error_code = "partyflow_unavailable"
    return PartyFlowApiError(
        status_code=exc.code,
        error_code=error_code,
        reason=reason,
        metadata=metadata,
    )


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized.isdigit():
        return None
    return max(1, int(normalized))
