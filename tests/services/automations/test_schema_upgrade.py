"""Schema upgrade coverage for automation webhook token persistence."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select, text
import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.bootstrap_runtime import LegacyWebhookSecretUpgradeError
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.automation import Automation
from afkbot.settings import Settings
from afkbot.services.automations.webhook_tokens import (
    hash_webhook_token,
    stored_webhook_token_ref,
)


async def test_create_schema_backfills_hash_refs_for_legacy_webhook_rows(
    tmp_path: Path,
) -> None:
    """Legacy webhook rows should not keep plaintext token values after schema upgrade."""

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
                            encrypted_webhook_token,
                            webhook_token_key_version,
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
        assert token == stored_webhook_token_ref(token_hash)
        assert token != "legacy-token"
        assert token_hash == hash_webhook_token("legacy-token")
        assert row[2] is None
        assert row[3] is None
        assert row[4] is None
        assert row[5] is None
        assert row[6] is None
        assert row[7] is None
        assert row[8] is None
    finally:
        await engine.dispose()


async def test_create_schema_encrypts_legacy_plaintext_webhook_tokens_when_vault_is_configured(
    tmp_path: Path,
) -> None:
    """Legacy plaintext webhook tokens should be encrypted before hash-ref replacement."""

    plaintext_token = "legacy-plaintext-token"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_plaintext_webhook_schema.db'}",
        root_dir=tmp_path,
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
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
                        webhook_token VARCHAR(255) UNIQUE,
                        webhook_token_hash VARCHAR(128) UNIQUE,
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
                    INSERT INTO automation_trigger_webhook (automation_id, webhook_token)
                    VALUES (1, :webhook_token)
                    """
                ),
                {"webhook_token": plaintext_token},
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
                            encrypted_webhook_token,
                            webhook_token_key_version
                        FROM automation_trigger_webhook
                        WHERE automation_id = 1
                        """
                    )
                )
            ).one()
        token_ref = str(row[0] or "")
        token_hash = str(row[1] or "")
        encrypted_token = str(row[2] or "")
        key_version = str(row[3] or "")
        assert token_hash == hash_webhook_token(plaintext_token)
        assert token_ref == stored_webhook_token_ref(token_hash)
        assert encrypted_token
        assert key_version.startswith("sha256:")

        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            result = await session.execute(select(Automation))
            automation = result.scalar_one()
        assert automation.id == 1
    finally:
        await engine.dispose()


async def test_create_schema_requires_vault_for_legacy_plaintext_webhook_tokens(
    tmp_path: Path,
) -> None:
    """Upgrade should fail closed until legacy plaintext webhook secrets can be encrypted."""

    db_path = tmp_path / "legacy_plaintext_preserved.db"
    plaintext_token = "legacy-preserve-me"
    initial_settings = Settings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        root_dir=tmp_path,
    )
    engine = create_engine(initial_settings)
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
                        webhook_token VARCHAR(255) UNIQUE,
                        webhook_token_hash VARCHAR(128) UNIQUE,
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
                    INSERT INTO automation_trigger_webhook (automation_id, webhook_token)
                    VALUES (1, :webhook_token)
                    """
                ),
                {"webhook_token": plaintext_token},
            )

        with pytest.raises(
            LegacyWebhookSecretUpgradeError,
            match="AFKBOT_CREDENTIALS_MASTER_KEYS",
        ):
            await create_schema(engine)
    finally:
        await engine.dispose()

    upgraded_settings = Settings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        root_dir=tmp_path,
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    upgraded_engine = create_engine(upgraded_settings)
    try:
        await create_schema(upgraded_engine)
        async with upgraded_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT
                            webhook_token,
                            webhook_token_hash,
                            encrypted_webhook_token,
                            webhook_token_key_version
                        FROM automation_trigger_webhook
                        WHERE automation_id = 1
                        """
                    )
                )
            ).one()
        token_ref = str(row[0] or "")
        token_hash = str(row[1] or "")
        assert token_hash == hash_webhook_token(plaintext_token)
        assert token_ref == stored_webhook_token_ref(token_hash)
        assert str(row[2] or "")
        assert str(row[3] or "").startswith("sha256:")
    finally:
        await upgraded_engine.dispose()


async def test_create_schema_adds_delivery_columns_for_legacy_automation_rows(
    tmp_path: Path,
) -> None:
    """Legacy automation tables should gain delivery columns without breaking ORM reads."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_automation_schema.db'}",
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
                    INSERT INTO profile (id, name, is_default, status, settings_json)
                    VALUES ('default', 'Default', 1, 'active', '{}')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO automation (id, profile_id, name, prompt, trigger_type, status)
                    VALUES (1, 'default', 'legacy-cron', 'legacy prompt', 'cron', 'active')
                    """
                )
            )

        await create_schema(engine)

        async with engine.connect() as conn:
            columns = {
                str(row[1]): str(row[4] or "")
                for row in (await conn.execute(text("PRAGMA table_info('automation')"))).fetchall()
            }
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT delivery_mode, delivery_target_json
                        FROM automation
                        WHERE id = 1
                        """
                    )
                )
            ).one()
        assert "delivery_mode" in columns
        assert "delivery_target_json" in columns
        assert row[0] == "tool"
        assert row[1] is None

        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            result = await session.execute(select(Automation))
            automation = result.scalar_one()
        assert automation.delivery_mode == "tool"
        assert automation.delivery_target_json is None
    finally:
        await engine.dispose()


async def test_create_schema_backfills_graph_runtime_columns_for_legacy_automation_rows(
    tmp_path: Path,
) -> None:
    """Legacy automation rows should gain graph runtime defaults without breaking ORM reads."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_graph_runtime.db'}",
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
                        execution_mode VARCHAR(16),
                        graph_fallback_mode VARCHAR(32),
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
                    INSERT INTO profile (id, name, is_default, status, settings_json)
                    VALUES ('default', 'Default', 1, 'active', '{}')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO automation (
                        id,
                        profile_id,
                        name,
                        prompt,
                        trigger_type,
                        status,
                        execution_mode,
                        graph_fallback_mode
                    )
                    VALUES (1, 'default', 'legacy-graph', 'legacy prompt', 'webhook', 'active', '', NULL)
                    """
                )
            )

        await create_schema(engine)

        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT execution_mode, graph_fallback_mode
                        FROM automation
                        WHERE id = 1
                        """
                    )
                )
            ).one()
        assert row[0] == "prompt"
        assert row[1] == "resume_with_ai_if_safe"

        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            automation = (await session.execute(select(Automation))).scalar_one()
        assert automation.execution_mode == "prompt"
        assert automation.graph_fallback_mode == "resume_with_ai_if_safe"
    finally:
        await engine.dispose()


async def test_create_schema_creates_graph_tables_without_breaking_legacy_rows(
    tmp_path: Path,
) -> None:
    """Schema creation should add graph tables while preserving legacy automation reads."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_graph_tables.db'}",
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
                    INSERT INTO profile (id, name, is_default, status, settings_json)
                    VALUES ('default', 'Default', 1, 'active', '{}')
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO automation (id, profile_id, name, prompt, trigger_type, status)
                    VALUES (1, 'default', 'legacy-graph', 'legacy prompt', 'cron', 'active')
                    """
                )
            )

        await create_schema(engine)

        async with engine.connect() as conn:
            table_names = {
                str(row[0])
                for row in (
                    await conn.execute(
                        text(
                            """
                            SELECT name
                            FROM sqlite_master
                            WHERE type = 'table'
                            """
                        )
                    )
                ).fetchall()
            }
        assert {
            "automation_flow",
            "automation_node_definition",
            "automation_node_version",
            "automation_node",
            "automation_edge",
            "automation_run",
            "automation_node_run",
            "automation_optimization_snapshot",
        }.issubset(table_names)

        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            automation = (await session.execute(select(Automation))).scalar_one()
        assert automation.id == 1
        assert automation.name == "legacy-graph"
    finally:
        await engine.dispose()


async def test_create_schema_backfills_graph_node_run_runtime_columns(
    tmp_path: Path,
) -> None:
    """Legacy graph ledgers should gain execution/effect columns during schema upgrade."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'legacy_graph_node_run.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    CREATE TABLE automation_run (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        automation_id INTEGER NOT NULL,
                        flow_id INTEGER,
                        profile_id VARCHAR(64) NOT NULL,
                        trigger_type VARCHAR(32) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        parent_session_id VARCHAR(255),
                        event_hash VARCHAR(128),
                        payload_json TEXT,
                        final_output_json TEXT,
                        fallback_status VARCHAR(32),
                        error_code VARCHAR(64),
                        reason TEXT,
                        started_at DATETIME,
                        completed_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE automation_node_run (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id INTEGER NOT NULL,
                        node_id INTEGER NOT NULL,
                        node_key VARCHAR(128) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 1,
                        selected_ports_json TEXT,
                        input_json TEXT,
                        output_json TEXT,
                        error_code VARCHAR(64),
                        reason TEXT,
                        child_task_id VARCHAR(128),
                        child_session_id VARCHAR(255),
                        child_run_id INTEGER,
                        started_at DATETIME,
                        completed_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        await create_schema(engine)

        async with engine.connect() as conn:
            columns = {
                str(row[1])
                for row in (await conn.execute(text("PRAGMA table_info('automation_node_run')"))).fetchall()
            }
        assert "execution_index" in columns
        assert "effects_json" in columns
    finally:
        await engine.dispose()
