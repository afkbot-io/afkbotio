"""File-backed provider token resolution helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.llm.token_verifier import token_expired_or_expiring_soon

TOKEN_SOURCE_SECRET = "secret"
TOKEN_SOURCE_FILE = "file"
OPENAI_CODEX_API_KEY_SOURCE_FIELD = "openai_codex_api_key_source"
OPENAI_CODEX_API_KEY_FILE_FIELD = "openai_codex_api_key_file"


@dataclass(frozen=True, slots=True)
class FileBackedProviderToken:
    """Resolved token plus the file it came from."""

    token: str
    path: Path


def provider_token_source_field(provider_id: str) -> str | None:
    """Return the runtime secret field controlling token source for one provider."""

    if provider_id.strip().lower() == LLMProviderId.OPENAI_CODEX.value:
        return OPENAI_CODEX_API_KEY_SOURCE_FIELD
    return None


def provider_token_file_field(provider_id: str) -> str | None:
    """Return the runtime secret field storing a token file path for one provider."""

    if provider_id.strip().lower() == LLMProviderId.OPENAI_CODEX.value:
        return OPENAI_CODEX_API_KEY_FILE_FIELD
    return None


def provider_token_file_secret_fields(provider_id: str) -> tuple[str, ...]:
    """Return all file-backed token metadata fields for one provider."""

    source_field = provider_token_source_field(provider_id)
    file_field = provider_token_file_field(provider_id)
    if source_field is None or file_field is None:
        return ()
    return (source_field, file_field)


def provider_supports_file_backed_token(provider_id: LLMProviderId) -> bool:
    """Return whether one provider supports live token reads from a local file."""

    return provider_id == LLMProviderId.OPENAI_CODEX


def openai_codex_file_token_runtime_secrets(path: Path) -> dict[str, str]:
    """Build runtime secret metadata for Codex file-backed token mode."""

    return {
        OPENAI_CODEX_API_KEY_SOURCE_FIELD: TOKEN_SOURCE_FILE,
        OPENAI_CODEX_API_KEY_FILE_FIELD: _stable_path_string(path),
    }


def openai_codex_secret_token_runtime_secrets() -> dict[str, str]:
    """Build runtime secret metadata for Codex encrypted-token mode."""

    return {OPENAI_CODEX_API_KEY_SOURCE_FIELD: TOKEN_SOURCE_SECRET}


def load_openai_codex_access_token_file(path: Path | str) -> str:
    """Read a usable Codex access token from an auth JSON or raw-token file."""

    token_path = Path(path).expanduser()
    try:
        raw = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not raw:
        return ""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _usable_token(raw)
    if not isinstance(payload, dict):
        return ""

    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        token = _usable_token(str(tokens.get("access_token") or "").strip())
        if token:
            return token
    return _usable_token(str(payload.get("access_token") or payload.get("OPENAI_API_KEY") or "").strip())


def discover_local_openai_codex_access_token_file() -> FileBackedProviderToken | None:
    """Find the first local Codex auth file that contains a usable access token."""

    for path in openai_codex_auth_file_candidates():
        token = load_openai_codex_access_token_file(path)
        if token:
            return FileBackedProviderToken(token=token, path=path)
    return None


def openai_codex_auth_file_candidates() -> tuple[Path, ...]:
    """Return ordered local Codex auth file candidates."""

    candidates: list[Path] = []
    codex_home = (os.getenv("CODEX_HOME") or "").strip()
    if codex_home:
        candidates.append(Path(codex_home).expanduser() / "auth.json")
    candidates.append(Path.home() / ".codex" / "auth.json")
    return tuple(dict.fromkeys(candidates))


def resolve_openai_codex_file_backed_api_key(
    *,
    source: object,
    path: object,
) -> str:
    """Resolve Codex token from file-backed settings when source=file."""

    if str(source or "").strip().lower() != TOKEN_SOURCE_FILE:
        return ""
    token_path = str(path or "").strip()
    if not token_path:
        return ""
    return load_openai_codex_access_token_file(token_path)


def normalize_token_source(value: object) -> str:
    """Normalize persisted token source values, falling back to secret mode."""

    normalized = str(value or "").strip().lower()
    if normalized in {TOKEN_SOURCE_SECRET, TOKEN_SOURCE_FILE}:
        return normalized
    return TOKEN_SOURCE_SECRET


def _usable_token(token: str) -> str:
    stripped = token.strip()
    if not stripped:
        return ""
    if token_expired_or_expiring_soon(token=stripped):
        return ""
    return stripped


def _stable_path_string(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))
