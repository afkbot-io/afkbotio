"""Persistent, bounded error logging for operator diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any

from afkbot.settings import Settings

_MAX_LOG_BYTES = 1_000_000
_BACKUP_COUNT = 5
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [pid=%(process)d] %(message)s"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SECRET_KEY_RE = re.compile(
    r"(?i)\b("
    r"authorization|[A-Za-z0-9_]*api[_-]?key|token|access[_-]?token|refresh[_-]?token|"
    r"secret|password|passwd|cookie"
    r")\b(\s*[:=]\s*)([\"']?)([^,\s\"']+)([\"']?)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_HANDLER_MARKER = "_afkbot_error_log_path"


class _RedactingFormatter(logging.Formatter):
    """Formatter that redacts common secret shapes from final log text."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def logs_dir(settings: Settings) -> Path:
    """Return the runtime log directory."""

    return settings.root_dir / "logs"


def component_log_path(settings: Settings, component: str) -> Path:
    """Return the component-specific error log path."""

    return logs_dir(settings) / _normalize_component(component) / "errors.log"


def list_log_files(settings: Settings) -> list[Path]:
    """Return known log files sorted by newest modification time first."""

    root = logs_dir(settings)
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if path.is_file() and path.name.startswith("errors.log")]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def configure_error_file_logging(*, settings: Settings, component: str = "runtime") -> Path:
    """Attach a rotating ERROR+ file handler to AFKBOT package loggers."""

    path = component_log_path(settings, component)
    for logger_name in ("afkbot", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        try:
            _attach_handler_once(logger, path)
        except OSError:
            continue
        if logger.level == logging.NOTSET or logger.level > logging.ERROR:
            logger.setLevel(logging.ERROR)
    return path


def log_exception(
    *,
    settings: Settings,
    component: str,
    message: str,
    exc: BaseException,
    context: Mapping[str, object] | None = None,
) -> Path:
    """Write one explicit exception report to a component error log."""

    path = component_log_path(settings, component)
    logger = logging.getLogger(f"afkbot.error_reports.{_normalize_component(component)}")
    try:
        _attach_handler_once(logger, path)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        logger.error(
            "%s%s",
            message,
            _format_context(context),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    except OSError:
        return path
    return path


def redact_log_text(value: str) -> str:
    """Redact common secret-like values from log text."""

    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _normalize_component(component: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", component.strip()).strip(".-/")
    return normalized or "runtime"


def _build_handler(path: Path) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    setattr(handler, _HANDLER_MARKER, str(path))
    handler.setLevel(logging.ERROR)
    handler.setFormatter(_RedactingFormatter(_LOG_FORMAT))
    return handler


def _attach_handler_once(logger: logging.Logger, path: Path) -> None:
    target_path = str(path)
    for existing in list(logger.handlers):
        if getattr(existing, _HANDLER_MARKER, None) == target_path:
            return
        if getattr(existing, _HANDLER_MARKER, None) is not None:
            logger.removeHandler(existing)
            existing.close()
    logger.addHandler(_build_handler(path))


def _format_context(context: Mapping[str, object] | None) -> str:
    if not context:
        return ""
    items = [
        f"{_safe_context_key(key)}={_safe_context_value(value)}"
        for key, value in sorted(context.items())
    ]
    return " | " + " ".join(items)


def _safe_context_key(value: object) -> str:
    return _SAFE_COMPONENT_RE.sub("_", str(value).strip()).strip("_") or "field"


def _safe_context_value(value: object) -> str:
    if isinstance(value, datetime):
        text = value.astimezone(timezone.utc).isoformat()
    else:
        text = str(value)
    return redact_log_text(text.replace("\n", "\\n").replace("\r", "\\r"))


def tail_log_file(path: Path, *, lines: int) -> str:
    """Return the last N lines from one UTF-8 log file."""

    if lines < 1:
        raise ValueError("lines must be >= 1")
    with path.open("rb") as file:
        file.seek(0, 2)
        size = file.tell()
        block_size = 4096
        data = b""
        cursor = size
        while cursor > 0 and data.count(b"\n") <= lines:
            read_size = min(block_size, cursor)
            cursor -= read_size
            file.seek(cursor)
            data = file.read(read_size) + data
    return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")


def remove_log_files(settings: Settings) -> list[Path]:
    """Remove current and rotated AFKBOT error logs."""

    removed: list[Path] = []
    for path in list_log_files(settings):
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def describe_log_file(path: Path, *, root: Path) -> dict[str, Any]:
    """Return compact metadata for CLI rendering."""

    stat = path.stat()
    return {
        "path": path,
        "relative_path": path.relative_to(root),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    }
