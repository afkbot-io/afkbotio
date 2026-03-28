"""Low-level Telegram Bot API HTTP helpers."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from afkbot.services.tools.workspace import (
    WorkspacePathResolutionError,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    resolve_workspace_path,
)
from afkbot.settings import Settings


async def _post_send_message(
    *,
    token: str,
    chat_id: str,
    text: str,
    message_thread_id: int | None,
    parse_mode: str | None,
    disable_web_page_preview: bool,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_send_message_sync,
            token=token,
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_send_media(
    *,
    settings: Settings,
    profile_id: str,
    token: str,
    chat_id: str,
    action: str,
    field_name: str,
    media_value: str,
    caption: str | None,
    message_thread_id: int | None,
    parse_mode: str | None,
    timeout_sec: int,
) -> dict[str, object]:
    local_path = await _resolve_workspace_media_path(
        settings=settings,
        profile_id=profile_id,
        raw_value=media_value,
    )
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_send_media_sync,
            token=token,
            chat_id=chat_id,
            action=action,
            field_name=field_name,
            media_value=media_value,
            local_path=local_path,
            caption=caption,
            message_thread_id=message_thread_id,
            parse_mode=parse_mode,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_get_me(*, token: str, timeout_sec: int) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _get_sync,
            token=token,
            method="getMe",
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_send_chat_action(
    *,
    token: str,
    chat_id: str,
    action: str,
    message_thread_id: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_send_chat_action_sync,
            token=token,
            chat_id=chat_id,
            action=action,
            message_thread_id=message_thread_id,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_get_updates(
    *,
    token: str,
    limit: int,
    timeout: int,
    offset: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_get_updates_sync,
            token=token,
            limit=limit,
            timeout=timeout,
            offset=offset,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_ban_chat_member(
    *,
    token: str,
    chat_id: str,
    user_id: int,
    revoke_messages: bool,
    until_date: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_ban_chat_member_sync,
            token=token,
            chat_id=chat_id,
            user_id=user_id,
            revoke_messages=revoke_messages,
            until_date=until_date,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_unban_chat_member(
    *,
    token: str,
    chat_id: str,
    user_id: int,
    only_if_banned: bool,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_unban_chat_member_sync,
            token=token,
            chat_id=chat_id,
            user_id=user_id,
            only_if_banned=only_if_banned,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


def _post_send_message_sync(
    *,
    token: str,
    chat_id: str,
    text: str,
    message_thread_id: int | None,
    parse_mode: str | None,
    disable_web_page_preview: bool,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")

    form: dict[str, str] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true" if disable_web_page_preview else "false",
    }
    if message_thread_id is not None:
        form["message_thread_id"] = str(message_thread_id)
    if parse_mode is not None and parse_mode.strip():
        form["parse_mode"] = parse_mode.strip()

    payload = _request_json(
        token=token,
        method="sendMessage",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram response missing result")
    return {
        "ok": True,
        "action": "send_message",
        "message_id": result.get("message_id"),
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
    }


def _post_send_chat_action_sync(
    *,
    token: str,
    chat_id: str,
    action: str,
    message_thread_id: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    normalized_action = action.strip().lower()
    if not normalized_action:
        raise ValueError("Telegram action is empty")

    form: dict[str, str] = {
        "chat_id": chat_id,
        "action": normalized_action,
    }
    if message_thread_id is not None:
        form["message_thread_id"] = str(message_thread_id)

    payload = _request_json(
        token=token,
        method="sendChatAction",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    if result is not True:
        raise RuntimeError("Telegram response missing ok result")
    return {
        "ok": True,
        "action": "send_chat_action",
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "chat_action": normalized_action,
    }


def _post_send_media_sync(
    *,
    token: str,
    chat_id: str,
    action: str,
    field_name: str,
    media_value: str,
    local_path: Path | None,
    caption: str | None,
    message_thread_id: int | None,
    parse_mode: str | None,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    normalized_action = action.strip().lower()
    if normalized_action not in {"send_photo", "send_document"}:
        raise ValueError(f"Unsupported Telegram media action: {action}")
    method = "sendPhoto" if normalized_action == "send_photo" else "sendDocument"
    form_fields: dict[str, str] = {"chat_id": chat_id}
    if caption:
        form_fields["caption"] = caption
    if message_thread_id is not None:
        form_fields["message_thread_id"] = str(message_thread_id)
    if parse_mode is not None and parse_mode.strip():
        form_fields["parse_mode"] = parse_mode.strip()
    if local_path is None:
        form_fields[field_name] = media_value
        payload = _request_json(
            token=token,
            method=method,
            request_data=urlencode(form_fields).encode("utf-8"),
            timeout_sec=timeout_sec,
        )
    else:
        file_name = local_path.name
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        payload = _request_json_multipart(
            token=token,
            method=method,
            fields=form_fields,
            file_field_name=field_name,
            file_path=local_path,
            file_name=file_name,
            mime_type=mime_type,
            timeout_sec=timeout_sec,
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram response missing result")
    return {
        "ok": True,
        "action": normalized_action,
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "message_id": result.get("message_id"),
        "file_name": local_path.name if local_path is not None else None,
    }


def _post_get_updates_sync(
    *,
    token: str,
    limit: int,
    timeout: int,
    offset: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    form: dict[str, str] = {
        "limit": str(limit),
        "timeout": str(timeout),
    }
    if offset is not None:
        form["offset"] = str(offset)
    payload = _request_json(
        token=token,
        method="getUpdates",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    updates = result if isinstance(result, list) else []
    return {
        "ok": True,
        "action": "get_updates",
        "updates": updates,
        "count": len(updates),
    }


def _post_ban_chat_member_sync(
    *,
    token: str,
    chat_id: str,
    user_id: int,
    revoke_messages: bool,
    until_date: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    if user_id <= 0:
        raise ValueError("Telegram user_id must be positive")

    form: dict[str, str] = {
        "chat_id": chat_id,
        "user_id": str(user_id),
        "revoke_messages": "true" if revoke_messages else "false",
    }
    if until_date is not None:
        form["until_date"] = str(until_date)
    _request_json(
        token=token,
        method="banChatMember",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    return {
        "ok": True,
        "action": "ban_chat_member",
        "chat_id": chat_id,
        "user_id": user_id,
        "revoke_messages": revoke_messages,
        "until_date": until_date,
    }


def _post_unban_chat_member_sync(
    *,
    token: str,
    chat_id: str,
    user_id: int,
    only_if_banned: bool,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    if user_id <= 0:
        raise ValueError("Telegram user_id must be positive")

    form: dict[str, str] = {
        "chat_id": chat_id,
        "user_id": str(user_id),
        "only_if_banned": "true" if only_if_banned else "false",
    }
    _request_json(
        token=token,
        method="unbanChatMember",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    return {
        "ok": True,
        "action": "unban_chat_member",
        "chat_id": chat_id,
        "user_id": user_id,
        "only_if_banned": only_if_banned,
    }


def _get_sync(*, token: str, method: str, timeout_sec: int) -> dict[str, object]:
    payload = _request_json(
        token=token,
        method=method,
        request_data=None,
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram response missing result")
    return {
        "ok": True,
        "action": "get_me",
        "id": result.get("id"),
        "username": result.get("username"),
        "is_bot": result.get("is_bot"),
    }


def _request_json(
    *,
    token: str,
    method: str,
    request_data: bytes | None,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    request = Request(
        url=f"https://api.telegram.org/bot{token}/{method}",
        method="POST" if request_data is not None else "GET",
        data=request_data,
        headers=(
            {"Content-Type": "application/x-www-form-urlencoded"}
            if request_data is not None
            else {}
        ),
    )
    with urlopen(request, timeout=float(timeout_sec)) as response:
        raw = response.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Telegram response payload")
    if payload.get("ok") is not True:
        description = payload.get("description")
        raise RuntimeError(f"Telegram API error: {description}")
    return {str(key): value for key, value in payload.items()}


def _request_json_multipart(
    *,
    token: str,
    method: str,
    fields: dict[str, str],
    file_field_name: str,
    file_path: Path,
    file_name: str,
    mime_type: str,
    timeout_sec: int,
) -> dict[str, object]:
    boundary = f"afkbot-{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field_name}"; '
            f'filename="{file_name}"\r\n'
        ).encode("utf-8")
    )
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = Request(
        url=f"https://api.telegram.org/bot{token}/{method}",
        method="POST",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request, timeout=float(timeout_sec)) as response:
        raw = response.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Telegram response payload")
    if payload.get("ok") is not True:
        description = payload.get("description")
        raise RuntimeError(f"Telegram API error: {description}")
    return {str(key): value for key, value in payload.items()}


async def _resolve_workspace_media_path(*, settings: Settings, profile_id: str, raw_value: str) -> Path | None:
    normalized = raw_value.strip()
    if not normalized:
        raise ValueError("Telegram media value is empty")
    if normalized.startswith(("http://", "https://")):
        return None
    looks_like_local_path = _looks_like_local_media_path(normalized)
    if not looks_like_local_path:
        return None
    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    scope_roots = await resolve_tool_workspace_scope_roots(settings=settings, profile_id=profile_id)
    try:
        path = resolve_workspace_path(
            base_dir=base_dir,
            scope_roots=scope_roots,
            raw_path=normalized,
            must_exist=True,
        )
    except WorkspacePathResolutionError as exc:
        if exc.code == "outside_scope":
            raise ValueError(f"Telegram media path is outside allowed workspace scope: {normalized}") from None
        if exc.code == "missing_path":
            raise ValueError(f"Telegram media path does not exist: {normalized}") from None
        raise ValueError(exc.reason) from None
    if not path.is_file():
        raise ValueError(f"Telegram media path is not a file: {normalized}")
    return path


def _looks_like_local_media_path(raw_value: str) -> bool:
    normalized = raw_value.strip()
    if not normalized:
        return False
    if normalized.startswith((".", "~", "/")) or "\\" in normalized:
        return True
    basename = Path(normalized).name
    if _looks_like_file_name(basename):
        return True
    if "/" in normalized:
        parts = tuple(part for part in normalized.split("/") if part)
        if any(part in {".", "..", "~"} for part in parts):
            return True
        if _looks_like_file_name(basename):
            return True
    return False


def _looks_like_file_name(raw_value: str) -> bool:
    normalized = raw_value.strip()
    if not normalized or normalized.startswith("."):
        return False
    if "." not in normalized:
        return False
    suffix = Path(normalized).suffix
    return bool(suffix and suffix != ".")
