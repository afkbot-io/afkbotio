"""Pytest shared configuration and test-runtime defaults."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.credentials import reset_credentials_services_async
from afkbot.services.automations import reset_automations_services_async
from afkbot.services.memory import reset_memory_services_async
from afkbot.services.profile_runtime.service import reset_profile_services_async
from afkbot.services.subagents import reset_subagent_services_async

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_test_db_url() -> str:
    """Return a repo-local sqlite URL that does not depend on system tempdir access."""

    runtime_dir = ROOT / "tmp" / "pytest-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{runtime_dir / 'afkbot-pytest.db'}"

# Most tests invoke CLI commands directly and do not need setup-first guard.
os.environ.setdefault("AFKBOT_SKIP_SETUP_GUARD", "1")
# Tests must not depend on runtime config from the developer's local install.
os.environ.setdefault(
    "AFKBOT_DB_URL",
    _default_test_db_url(),
)


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    """Dispose cached async services so suite shutdown does not leak aiosqlite worker threads."""

    _ = session, exitstatus
    asyncio.run(reset_channel_endpoint_services_async())
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_automations_services_async())
    asyncio.run(reset_credentials_services_async())
    asyncio.run(reset_memory_services_async())
    asyncio.run(reset_profile_services_async())
    asyncio.run(reset_subagent_services_async())
