"""Import smoke tests for CLI startup paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_cli_main_import_does_not_trigger_startup_cycle() -> None:
    """CLI main import should succeed without circular-import crashes."""

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "from afkbot.cli.main import run"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
