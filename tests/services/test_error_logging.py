"""Tests for persistent AFKBOT error logging helpers."""

from __future__ import annotations

import logging

from afkbot.services.error_logging import (
    component_log_path,
    configure_error_file_logging,
    log_exception,
    logs_dir,
    redact_log_text,
)
from afkbot.settings import Settings


def test_component_log_path_resolves_under_runtime_root(tmp_path) -> None:
    """Log files should live under the runtime root, grouped by component."""

    settings = Settings(root_dir=tmp_path)

    assert logs_dir(settings) == tmp_path / "logs"
    assert component_log_path(settings, "api") == tmp_path / "logs" / "api" / "errors.log"
    assert component_log_path(settings, "../bad component") == (
        tmp_path / "logs" / "bad-component" / "errors.log"
    )


def test_configure_error_file_logging_writes_redacted_error(tmp_path) -> None:
    """Existing package loggers should write ERROR+ messages to a bounded file."""

    settings = Settings(root_dir=tmp_path)
    logger = logging.getLogger("afkbot.tests.error_logging")

    configure_error_file_logging(settings=settings, component="runtime")
    logger.error("provider token=%s failed", "secret-token-123")

    log_path = component_log_path(settings, "runtime")
    contents = log_path.read_text(encoding="utf-8")
    assert "provider token=[REDACTED] failed" in contents
    assert "secret-token-123" not in contents


def test_log_exception_writes_component_context(tmp_path) -> None:
    """Explicit exception reports should include safe context and traceback."""

    settings = Settings(root_dir=tmp_path)

    try:
        raise RuntimeError("boom password=hunter2")
    except RuntimeError as exc:
        log_exception(
            settings=settings,
            component="api",
            message="Unhandled API exception",
            exc=exc,
            context={"method": "POST", "path": "/v1/tasks", "authorization": "Bearer x"},
        )

    contents = component_log_path(settings, "api").read_text(encoding="utf-8")
    assert "Unhandled API exception" in contents
    assert "method=POST" in contents
    assert "path=/v1/tasks" in contents
    assert "RuntimeError: boom password=[REDACTED]" in contents
    assert "authorization=[REDACTED]" in contents
    assert "hunter2" not in contents
    assert "Bearer x" not in contents


def test_redact_log_text_handles_common_secret_shapes() -> None:
    """Secret-like key/value pairs and bearer tokens should not be copied to files."""

    redacted = redact_log_text(
        "OPENAI_API_KEY=sk-test token: abc Authorization: Bearer token123 password='pw'"
    )

    assert "sk-test" not in redacted
    assert "abc" not in redacted
    assert "token123" not in redacted
    assert "'pw'" not in redacted
    assert "[REDACTED]" in redacted
