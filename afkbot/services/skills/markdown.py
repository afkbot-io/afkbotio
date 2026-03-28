"""Helpers for parsing and normalizing skill markdown files."""

from __future__ import annotations

import json
import re
from typing import TypeAlias

_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_SKIP_FRONTMATTER_KEYS = {"name", "description", "summary"}
FrontmatterValue: TypeAlias = str | bool | list[str]


def split_frontmatter(content: str) -> tuple[list[str] | None, str]:
    """Return raw frontmatter lines and markdown body."""

    if not content.startswith("---\n"):
        return None, content

    end = content.find("\n---\n", 4)
    if end == -1:
        return None, content

    raw = content[4:end]
    body = content[end + len("\n---\n") :]
    return raw.splitlines(), body


def parse_frontmatter(content: str) -> dict[str, FrontmatterValue]:
    """Parse simple top-level frontmatter keys from skill markdown."""

    raw_lines, _ = split_frontmatter(content)
    if raw_lines is None:
        return {}

    metadata: dict[str, FrontmatterValue] = {}
    index = 0
    while index < len(raw_lines):
        raw = raw_lines[index]
        stripped = raw.strip()
        if not stripped or raw[0].isspace():
            index += 1
            continue
        match = _TOP_LEVEL_KEY_RE.match(raw)
        if match is None:
            index += 1
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        if value:
            metadata[key] = _parse_inline_value(value)
            index += 1
            continue

        block: list[str] = []
        index += 1
        while index < len(raw_lines):
            nested = raw_lines[index]
            if nested and not nested[0].isspace():
                break
            block.append(nested)
            index += 1
        metadata[key] = _parse_block_value(block)
    return metadata


def extract_summary(content: str) -> str:
    """Extract a deterministic summary, preferring frontmatter description."""

    metadata = parse_frontmatter(content)
    for key in ("description", "summary"):
        candidate = _normalize_line(_string_value(metadata.get(key, "")))
        if candidate:
            return candidate

    _, body = split_frontmatter(content)
    return _extract_legacy_summary(body)


def canonicalize_skill_markdown(*, name: str, content: str) -> str:
    """Rewrite skill markdown to include canonical name/description frontmatter."""

    raw_frontmatter_lines, body = split_frontmatter(content)
    metadata = parse_frontmatter(content)

    description = _normalize_line(_string_value(metadata.get("description", "")))
    if not description:
        description = _normalize_line(_string_value(metadata.get("summary", "")))
    if not description:
        description = _extract_legacy_summary(body if raw_frontmatter_lines is not None else content)
    if not description:
        description = f"Use this skill when the task explicitly requires `{name}`."

    extra_lines: list[str] = []
    if raw_frontmatter_lines is not None:
        for raw in raw_frontmatter_lines:
            match = _TOP_LEVEL_KEY_RE.match(raw) if raw and not raw[0].isspace() else None
            if match is not None and match.group(1).strip() in _SKIP_FRONTMATTER_KEYS:
                continue
            extra_lines.append(raw.rstrip())

    normalized_body = (body if raw_frontmatter_lines is not None else content).strip()
    if not normalized_body:
        normalized_body = f"# {name}"
    elif not _starts_with_heading(normalized_body):
        normalized_body = f"# {name}\n\n{normalized_body}"

    frontmatter_lines = [f"name: {name}", f"description: {json.dumps(description, ensure_ascii=False)}"]
    for raw in extra_lines:
        frontmatter_lines.append(raw)

    frontmatter = "\n".join(frontmatter_lines).rstrip()
    return f"---\n{frontmatter}\n---\n\n{normalized_body}\n"


def _extract_legacy_summary(body: str) -> str:
    """Extract a stable summary from legacy body-only skill markdown."""

    first_heading: str | None = None
    skipped_primary_heading = False

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            text = _normalize_line(line.lstrip("#").strip())
            if not text:
                continue
            if first_heading is None:
                first_heading = text
            if not skipped_primary_heading:
                skipped_primary_heading = True
                continue
            continue
        return _normalize_line(line)

    return first_heading or ""


def _starts_with_heading(content: str) -> bool:
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        return line.startswith("#")
    return False


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_inline_value(value: str) -> FrontmatterValue:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if normalized.startswith("[") and normalized.endswith("]"):
        inner = normalized[1:-1].strip()
        if not inner:
            return []
        return [
            _strip_wrapping_quotes(item.strip())
            for item in inner.split(",")
            if item.strip()
        ]
    return _strip_wrapping_quotes(normalized)


def _parse_block_value(lines: list[str]) -> FrontmatterValue:
    items: list[str] = []
    non_empty: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        non_empty.append(stripped)
        if stripped.startswith("- "):
            items.append(_strip_wrapping_quotes(stripped[2:].strip()))
    if items and len(items) == len(non_empty):
        return items
    return " ".join(non_empty)


def _string_value(value: FrontmatterValue | object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, str):
        return value
    return ""


def _normalize_line(value: str) -> str:
    return " ".join(value.split())
