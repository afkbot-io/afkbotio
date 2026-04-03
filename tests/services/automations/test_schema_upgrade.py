"""Schema upgrade coverage for automation webhook token persistence."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.settings import Settings
from afkbot.services.automations.webhook_tokens import hash_webhook_token


async def test_create_schema_backfills_plaintext_tokens_for_legacy_webhook_rows(
    tmp_path: Path,
) -> None:
    """Legacy webhook rows without plaintext tokens should receive a usable replacement token."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_webhook_schema.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE profile (
                        id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        is_default BOOLEAN NOT NULL DEFAULT 0,
                        status VARCHAR(32) NOT NULL DEFAULT 'active',
                        settings_json TEXT NOT NULL DEFAULT '{}',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE automation (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id VARCHAR(64) NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        prompt TEXT NOT NULL,
                        trigger_type VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(profile_id) REFERENCES profile(id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE automation_trigger_webhook (
                        automation_id INTEGER PRIMARY KEY,
                        webhook_token_hash VARCHAR(128) UNIQUE,
                        last_event_hash VARCHAR(128),
                        in_progress_event_hash VARCHAR(128),
                        claim_token VARCHAR(64),
                        in_progress_until DATETIME,
                        last_session_id VARCHAR(255),
                        last_received_at DATETIME,
                        FOREIGN KEY(automation_id) REFERENCES automation(id)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO profile (id, name, is_default, status, settings_json)
                    VALUES ('default', 'Default', 1, 'active', '{}')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO automation (id, profile_id, name, prompt, trigger_type, status)
                    VALUES (1, 'default', 'legacy-hook', 'legacy prompt', 'webhook', 'active')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO automation_trigger_webhook (automation_id, webhook_token_hash)
                    VALUES (1, :token_hash)
                    """
                ),
                {"token_hash": hash_webhook_token("legacy-token")},
            )

        await create_schema(engine)

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT
                            webhook_token,
                            webhook_token_hash,
                            last_session_id,
                            last_started_at,
                            last_succeeded_at,
                            last_failed_at,
                            last_error
                        FROM automation_trigger_webhook
                        WHERE automation_id = 1
                        """
                    )
                )
            ).one()
        token = str(row[0] or "")
        token_hash = str(row[1] or "")
        assert token
        assert token != "legacy-token"
        assert token_hash == hash_webhook_token(token)
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None
        assert row[5] is None
        assert row[6] is None
    finally:
        await engine.dispose()
