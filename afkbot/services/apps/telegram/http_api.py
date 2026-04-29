"""Low-level Telegram Bot API HTTP helpers."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import uuid
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from afkbot.services.channels.media_ingest import safe_filename
from afkbot.services.channels.media_ingest import resolve_channel_outbound_media_path
from afkbot.services.tools.workspace import (
    resolve_tool_workspace_base_dir,
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
    reply_markup: dict[str, object] | None,
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
            reply_markup=reply_markup,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_send_message_draft(
    *,
    token: str,
    chat_id: str,
    draft_id: int,
    text: str,
    message_thread_id: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_send_message_draft_sync,
            token=token,
            chat_id=chat_id,
            draft_id=draft_id,
            text=text,
            message_thread_id=message_thread_id,
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
    reply_markup: dict[str, object] | None,
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
            reply_markup=reply_markup,
            max_upload_bytes=settings.channel_media_upload_max_bytes,
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


async def _post_answer_callback_query(
    *,
    token: str,
    callback_query_id: str,
    text: str | None,
    show_alert: bool,
    timeout_sec: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_answer_callback_query_sync,
            token=token,
            callback_query_id=callback_query_id,
            text=text,
            show_alert=show_alert,
            timeout_sec=timeout_sec,
        ),
        timeout=float(timeout_sec),
    )


async def _post_download_file(
    *,
    settings: Settings,
    profile_id: str,
    token: str,
    file_id: str,
    destination_dir: str,
    suggested_file_name: str | None,
    timeout_sec: int,
    max_bytes: int,
) -> dict[str, object]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            _post_download_file_sync,
            settings=settings,
            profile_id=profile_id,
            token=token,
            file_id=file_id,
            destination_dir=destination_dir,
            suggested_file_name=suggested_file_name,
            timeout_sec=timeout_sec,
            max_bytes=max_bytes,
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
    reply_markup: dict[str, object] | None,
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
    if reply_markup:
        form["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

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


def _post_send_message_draft_sync(
    *,
    token: str,
    chat_id: str,
    draft_id: int,
    text: str,
    message_thread_id: int | None,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    if draft_id == 0:
        raise ValueError("Telegram draft_id must be non-zero")
    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("Telegram draft text is empty")

    form: dict[str, str] = {
        "chat_id": chat_id,
        "draft_id": str(draft_id),
        "text": normalized_text,
    }
    if message_thread_id is not None:
        form["message_thread_id"] = str(message_thread_id)
    payload = _request_json(
        token=token,
        method="sendMessageDraft",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    if result is not True:
        raise RuntimeError("Telegram response missing ok result")
    return {
        "ok": True,
        "action": "send_message_draft",
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "draft_id": draft_id,
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


def _post_answer_callback_query_sync(
    *,
    token: str,
    callback_query_id: str,
    text: str | None,
    show_alert: bool,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    normalized_id = callback_query_id.strip()
    if not normalized_id:
        raise ValueError("Telegram callback_query_id is empty")
    form: dict[str, str] = {
        "callback_query_id": normalized_id,
        "show_alert": "true" if show_alert else "false",
    }
    if text is not None and text.strip():
        form["text"] = text.strip()
    payload = _request_json(
        token=token,
        method="answerCallbackQuery",
        request_data=urlencode(form).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = payload.get("result")
    if result is not True:
        raise RuntimeError("Telegram response missing ok result")
    return {
        "ok": True,
        "action": "answer_callback_query",
        "callback_query_id": normalized_id,
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
    reply_markup: dict[str, object] | None,
    max_upload_bytes: int,
    timeout_sec: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    if not chat_id.strip():
        raise ValueError("Telegram chat_id is empty")
    normalized_action = action.strip().lower()
    method_by_action = {
        "send_photo": "sendPhoto",
        "send_document": "sendDocument",
        "send_voice": "sendVoice",
        "send_audio": "sendAudio",
        "send_video": "sendVideo",
        "send_animation": "sendAnimation",
        "send_sticker": "sendSticker",
    }
    if normalized_action not in method_by_action:
        raise ValueError(f"Unsupported Telegram media action: {action}")
    method = method_by_action[normalized_action]
    form_fields: dict[str, str] = {"chat_id": chat_id}
    if caption and normalized_action != "send_sticker":
        form_fields["caption"] = caption
    if message_thread_id is not None:
        form_fields["message_thread_id"] = str(message_thread_id)
    if parse_mode is not None and parse_mode.strip() and normalized_action != "send_sticker":
        form_fields["parse_mode"] = parse_mode.strip()
    if reply_markup:
        form_fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if local_path is None:
        form_fields[field_name] = media_value
        payload = _request_json(
            token=token,
            method=method,
            request_data=urlencode(form_fields).encode("utf-8"),
            timeout_sec=timeout_sec,
        )
    else:
        size_bytes = local_path.stat().st_size
        upload_limit = _telegram_local_upload_limit_bytes(
            action=normalized_action,
            configured_limit=max_upload_bytes,
        )
        if size_bytes > upload_limit:
            raise ValueError(
                f"Telegram media file exceeds max upload size: {size_bytes} > {upload_limit}"
            )
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


def _telegram_local_upload_limit_bytes(*, action: str, configured_limit: int) -> int:
    telegram_limit = 10_000_000 if action == "send_photo" else 50_000_000
    return min(max(1, configured_limit), telegram_limit)


def _post_download_file_sync(
    *,
    settings: Settings,
    profile_id: str,
    token: str,
    file_id: str,
    destination_dir: str,
    suggested_file_name: str | None,
    timeout_sec: int,
    max_bytes: int,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError("Telegram token is empty")
    normalized_file_id = file_id.strip()
    if not normalized_file_id:
        raise ValueError("Telegram file_id is empty")
    file_payload = _request_json(
        token=token,
        method="getFile",
        request_data=urlencode({"file_id": normalized_file_id}).encode("utf-8"),
        timeout_sec=timeout_sec,
    )
    result = file_payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Telegram getFile response missing result")
    file_path = result.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        raise RuntimeError("Telegram getFile response missing file_path")
    file_size = result.get("file_size")
    if isinstance(file_size, int) and file_size > max_bytes:
        raise ValueError(f"Telegram file exceeds max download size: {file_size} > {max_bytes}")

    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    scope_roots = tuple(root.resolve(strict=False) for root in [base_dir])
    destination = resolve_workspace_path(
        base_dir=base_dir,
        scope_roots=scope_roots,
        raw_path=destination_dir,
        must_exist=False,
    )
    destination.mkdir(parents=True, exist_ok=True)
    file_name = safe_filename(suggested_file_name or Path(file_path).name)
    local_path = destination / file_name
    download_url = f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/')}"
    downloaded = _download_url_to_path(
        url=download_url,
        path=local_path,
        timeout_sec=timeout_sec,
        max_bytes=max_bytes,
    )
    mime_type = mimetypes.guess_type(local_path.name)[0]
    return {
        "ok": True,
        "action": "download_file",
        "file_id": normalized_file_id,
        "file_path": file_path,
        "path": local_path.resolve(strict=False).relative_to(base_dir.resolve(strict=False)).as_posix(),
        "file_name": local_path.name,
        "mime_type": mime_type,
        "size_bytes": downloaded,
    }


def _download_url_to_path(
    *,
    url: str,
    path: Path,
    timeout_sec: int,
    max_bytes: int,
) -> int:
    request = Request(url=url, method="GET")
    downloaded = 0
    with urlopen(request, timeout=float(timeout_sec)) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > max_bytes:
            raise ValueError(f"Telegram file exceeds max download size: {content_length} > {max_bytes}")
        with path.open("wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    handle.close()
                    path.unlink(missing_ok=True)
                    raise ValueError(f"Telegram file exceeds max download size: {downloaded} > {max_bytes}")
                handle.write(chunk)
    return downloaded


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
    return await resolve_channel_outbound_media_path(
        settings=settings,
        profile_id=profile_id,
        raw_value=raw_value,
        label="Telegram media",
    )
