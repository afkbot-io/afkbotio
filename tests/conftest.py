"""Pytest shared configuration and test-runtime defaults."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.channels.ingress_journal import reset_channel_ingress_journal_services_async
from afkbot.services.channels.ingress_persistence import reset_channel_ingress_pending_services_async
from afkbot.services.credentials import reset_credentials_services_async
from afkbot.services.automations import reset_automations_services_async
from afkbot.services.memory import reset_memory_services_async
from afkbot.services.profile_runtime.service import reset_profile_services_async
from afkbot.services.subagents import reset_subagent_services_async
from afkbot.services.task_flow import reset_task_flow_services_async

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_test_db_url() -> str:
    """Return a repo-local sqlite URL that does not depend on system tempdir access."""

    runtime_dir = ROOT / "tmp" / "pytest-runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{runtime_dir / 'afkbot-pytest.db'}"

# Tests must not depend on runtime config from the developer's local install.
os.environ.setdefault(
    "AFKBOT_DB_URL",
    _default_test_db_url(),
)


async def _reset_cached_async_services() -> None:
    """Dispose cached async services on one event loop to avoid shutdown races."""

    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_automations_services_async()
    await reset_credentials_services_async()
    await reset_memory_services_async()
    await reset_profile_services_async()
    await reset_subagent_services_async()
    await reset_task_flow_services_async()
    await asyncio.sleep(0.05)


def pytest_runtest_teardown(item, nextitem) -> None:  # type: ignore[no-untyped-def]
    """Dispose loop-bound cached services after CLI/tool tests that use fresh event loops."""

    _ = nextitem
    path = item.path.as_posix()
    if "tests/cli/" not in path and "tests/services/tools/" not in path:
        return
    asyncio.run(_reset_cached_async_services())


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    """Dispose cached async services so suite shutdown does not leak aiosqlite worker threads."""

    _ = session, exitstatus
    asyncio.run(_reset_cached_async_services())
