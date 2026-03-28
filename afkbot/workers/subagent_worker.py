"""Detached process entrypoint for persisted subagent task execution."""

from __future__ import annotations

import argparse
import asyncio

from afkbot.services.subagents import get_subagent_service
from afkbot.settings import get_settings


async def _run_task(*, task_id: str) -> bool:
    settings = get_settings()
    service = get_subagent_service(settings)
    return await service.execute_persisted_task(task_id=task_id)


def main() -> int:
    """Run one persisted subagent task and return process exit code."""

    parser = argparse.ArgumentParser(description="AFKBOT subagent worker")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    success = asyncio.run(_run_task(task_id=str(args.task_id)))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

