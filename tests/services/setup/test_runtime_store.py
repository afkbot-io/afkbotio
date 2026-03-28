"""Tests for runtime config/secrets store."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from afkbot.services.setup.runtime_store import read_runtime_secrets, write_runtime_secrets
from afkbot.services.upgrade import UpgradeService
from afkbot.settings import Settings


def test_write_runtime_secrets_encrypts_payload(tmp_path: Path) -> None:
    """Runtime secrets payload should be encrypted at rest and readable back."""

    settings = Settings(root_dir=tmp_path)
    write_runtime_secrets(
        settings,
        secrets={
            "llm_api_key": "secret-api-key",
            "credentials_master_keys": "secret-vault-key",
        },
    )

    raw = settings.runtime_secrets_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["encryption"] == "fernet-v1"
    assert "ciphertext" in payload
    assert "secrets" not in payload
    assert "secret-api-key" not in raw
    assert settings.runtime_secrets_key_path.exists()

    restored = read_runtime_secrets(settings)
    assert restored["llm_api_key"] == "secret-api-key"
    assert restored["credentials_master_keys"] == "secret-vault-key"


def test_upgrade_service_migrates_legacy_plaintext_runtime_secrets(tmp_path: Path) -> None:
    """Upgrade service should migrate legacy plaintext runtime secrets to encrypted payloads."""

    settings = Settings(root_dir=tmp_path)
    settings.runtime_secrets_path.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime_secrets_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-05T00:00:00+00:00",
                "secrets": {"llm_api_key": "legacy-value"},
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    service = UpgradeService(settings)
    try:
        report = asyncio.run(service.apply())
    finally:
        asyncio.run(service.shutdown())

    restored = read_runtime_secrets(settings)
    assert report.changed is True
    assert restored["llm_api_key"] == "legacy-value"
    payload = json.loads(settings.runtime_secrets_path.read_text(encoding="utf-8"))
    assert payload["encryption"] == "fernet-v1"
    assert "secrets" not in payload


def test_read_runtime_secrets_supports_legacy_plaintext_payload_before_upgrade(tmp_path: Path) -> None:
    """Legacy plaintext runtime secrets should still be readable before upgrade apply runs."""

    settings = Settings(root_dir=tmp_path)
    settings.runtime_secrets_path.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime_secrets_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-05T00:00:00+00:00",
                "secrets": {"llm_api_key": "legacy-value"},
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    restored = read_runtime_secrets(settings)

    assert restored["llm_api_key"] == "legacy-value"
