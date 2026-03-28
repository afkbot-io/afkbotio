"""Manifest parsing and path helpers for the AFKBOT skill loader."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import cast

from afkbot.services.skills.loader_contracts import SkillExecutionMode, SkillManifest
from afkbot.services.skills.markdown import FrontmatterValue, extract_summary, parse_frontmatter
from afkbot.services.skills.normalization import infer_manifest_hints


SKILL_MANIFEST_FILENAME = "AFKBOT.skill.toml"
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_skill_name(name: str) -> None:
    """Validate one user-provided skill name against the safe path pattern."""

    if not SKILL_NAME_RE.match(name):
        raise ValueError(f"Invalid skill name: {name}")


def safe_skill_path(root: Path, name: str) -> Path:
    """Build one in-scope skill markdown path and reject traversal."""

    root_resolved = root.resolve()
    try:
        candidate = (root_resolved / name / "SKILL.md").resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid skill path: {name}") from exc
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"Invalid skill path: {name}")
    return candidate


def manifest_path_for_skill(path: Path) -> Path:
    """Return the adjacent AFKBOT manifest path for one skill markdown file."""

    return path.parent / SKILL_MANIFEST_FILENAME


def safe_manifest_path(root: Path, name: str) -> Path:
    """Build one in-scope skill manifest path and reject traversal."""

    root_resolved = root.resolve()
    try:
        candidate = (root_resolved / name / SKILL_MANIFEST_FILENAME).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid skill manifest path: {name}") from exc
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"Invalid skill manifest path: {name}")
    return candidate


def load_overlay(path: Path) -> tuple[dict[str, object], tuple[str, ...]]:
    """Load and normalize one optional AFKBOT skill manifest overlay."""

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}, ("read_error",)
    except tomllib.TOMLDecodeError:
        return {}, ("parse_error",)
    if not isinstance(raw, dict):
        return {}, ("invalid_root",)
    if raw.get("manifest_version") != 1:
        return {}, ("invalid_manifest_version",)

    normalized: dict[str, object] = {
        "description": raw.get("description"),
        "aliases": raw.get("aliases"),
        "triggers": raw.get("triggers"),
        "tool_names": raw.get("tool_names"),
        "app_names": raw.get("app_names"),
        "preferred_tool_order": raw.get("preferred_tool_order"),
        "always_on": raw.get("always_on"),
        "advisory_only": raw.get("advisory_only"),
        "execution_mode": raw.get("execution_mode"),
    }
    requires = raw.get("requires")
    if isinstance(requires, dict):
        normalized["requires_bins"] = requires.get("bins")
        normalized["requires_env"] = requires.get("env")
        normalized["requires_python_packages"] = requires.get("python_packages")
    suggested = raw.get("suggested")
    if isinstance(suggested, dict):
        normalized["suggested_bins"] = suggested.get("bins")
    source = raw.get("source")
    if isinstance(source, dict):
        normalized["source_kind"] = source.get("kind")
        normalized["source_id"] = source.get("id")
        normalized["source_url"] = source.get("url")
    return normalized, ()


def build_manifest(
    *,
    name: str,
    content: str,
    metadata: dict[str, FrontmatterValue],
    overlay: dict[str, object] | None = None,
) -> SkillManifest:
    """Build one normalized machine-readable manifest from markdown and overlay data."""

    overlay = overlay or {}
    inferred = infer_manifest_hints(content=content, metadata=metadata)
    tool_names = normalize_list(
        overlay.get("tool_names", metadata.get("tool_names")),
        lowercase=True,
    )
    app_names = normalize_list(
        overlay.get("app_names", metadata.get("app_names")),
        lowercase=True,
    )
    execution_mode = infer_execution_mode(
        value=overlay.get("execution_mode", metadata.get("execution_mode")),
        legacy_advisory_only=overlay.get("advisory_only", metadata.get("advisory_only")),
        has_surface=bool(tool_names or app_names),
    )
    return SkillManifest(
        name=name,
        description=normalize_line(str(overlay.get("description") or "").strip())
        or (extract_summary(content) if content else ""),
        aliases=normalize_list(
            overlay.get("aliases", metadata.get("aliases")),
            lowercase=True,
            validator=SKILL_NAME_RE.match,
        ),
        triggers=normalize_list(
            overlay.get("triggers", metadata.get("triggers")),
            lowercase=True,
        ),
        tool_names=tool_names,
        app_names=app_names,
        preferred_tool_order=normalize_list(
            overlay.get("preferred_tool_order", metadata.get("preferred_tool_order")),
            lowercase=True,
        ),
        always_on=parse_bool(overlay.get("always_on", metadata.get("always_on", False))),
        execution_mode=execution_mode,
        requires_bins=normalize_list(
            overlay.get("requires_bins", metadata.get("requires_bins")),
        ),
        suggested_bins=normalize_list(
            overlay.get("suggested_bins", metadata.get("suggested_bins")),
        )
        or inferred.suggested_bins,
        requires_env=normalize_list(
            overlay.get("requires_env", metadata.get("requires_env")),
        ),
        requires_python_packages=normalize_list(
            overlay.get("requires_python_packages", metadata.get("requires_python_packages")),
        )
        or inferred.requires_python_packages,
        source_kind=normalize_line(str(overlay.get("source_kind") or "")),
        source_id=normalize_line(str(overlay.get("source_id") or "")),
        source_url=normalize_line(str(overlay.get("source_url") or "")),
    )


def build_default_manifest(
    *,
    name: str,
    content: str,
    source_kind: str = "",
    source_id: str = "",
    source_url: str = "",
) -> SkillManifest:
    """Build one default AFKBOT manifest directly from source markdown."""

    metadata = parse_frontmatter(content)
    return build_manifest(
        name=name,
        content=content,
        metadata=metadata,
        overlay={
            "source_kind": source_kind,
            "source_id": source_id,
            "source_url": source_url,
        },
    )


def render_manifest_toml(manifest: SkillManifest) -> str:
    """Render one AFKBOT skill manifest overlay as TOML."""

    lines: list[str] = [
        "manifest_version = 1",
        f"name = {json.dumps(manifest.name, ensure_ascii=False)}",
        f"description = {json.dumps(manifest.description, ensure_ascii=False)}",
        f"execution_mode = {json.dumps(manifest.execution_mode)}",
        f'always_on = {"true" if manifest.always_on else "false"}',
        f"aliases = {render_toml_list(manifest.aliases)}",
        f"triggers = {render_toml_list(manifest.triggers)}",
        f"tool_names = {render_toml_list(manifest.tool_names)}",
        f"app_names = {render_toml_list(manifest.app_names)}",
        f"preferred_tool_order = {render_toml_list(manifest.preferred_tool_order)}",
        "",
        "[requires]",
        f"bins = {render_toml_list(manifest.requires_bins)}",
        f"env = {render_toml_list(manifest.requires_env)}",
        f"python_packages = {render_toml_list(manifest.requires_python_packages)}",
        "",
        "[suggested]",
        f"bins = {render_toml_list(manifest.suggested_bins)}",
        "",
        "[source]",
        f"kind = {json.dumps(manifest.source_kind, ensure_ascii=False)}",
        f"id = {json.dumps(manifest.source_id, ensure_ascii=False)}",
        f"url = {json.dumps(manifest.source_url, ensure_ascii=False)}",
        "",
    ]
    return "\n".join(lines)


def render_toml_list(values: tuple[str, ...]) -> str:
    """Render one string tuple as a TOML array."""

    return "[" + ", ".join(json.dumps(value, ensure_ascii=False) for value in values) + "]"


def parse_list(value: FrontmatterValue | object) -> set[str]:
    """Parse one normalized metadata field into a unique set."""

    if value is None or value is False:
        return set()
    if isinstance(value, list):
        items = value
    elif isinstance(value, bool):
        return set()
    else:
        items = str(value).split(",")
    return {str(item).strip() for item in items if str(item).strip()}


def normalize_list(
    value: FrontmatterValue | object,
    *,
    lowercase: bool = False,
    validator: re.Pattern[str] | Callable[[str], object] | None = None,
) -> tuple[str, ...]:
    """Normalize one metadata list while preserving first-seen order."""

    items: list[str]
    if value is None or value is False:
        items = []
    elif isinstance(value, list):
        items = [str(item) for item in value]
    elif isinstance(value, bool):
        items = []
    else:
        items = str(value).split(",")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        candidate = raw.strip()
        if lowercase:
            candidate = candidate.lower()
        if not candidate or candidate == "." or candidate in seen:
            continue
        if validator is not None:
            valid = validator(candidate) if callable(validator) else validator.match(candidate)
            if not valid:
                continue
        normalized.append(candidate)
        seen.add(candidate)
    return tuple(normalized)


def parse_bool(value: FrontmatterValue | object) -> bool:
    """Parse one permissive metadata boolean."""

    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return False


def infer_execution_mode(
    *,
    value: FrontmatterValue | object,
    legacy_advisory_only: FrontmatterValue | object,
    has_surface: bool,
) -> SkillExecutionMode:
    """Infer one canonical skill execution mode from manifest metadata."""

    normalized = str(value or "").strip().lower()
    if normalized in {"advisory", "executable", "dispatch"}:
        return cast(SkillExecutionMode, normalized)
    if parse_bool(legacy_advisory_only):
        return "advisory"
    return "executable" if has_surface else "advisory"


def normalize_line(value: str) -> str:
    """Collapse whitespace in one free-form manifest line."""

    return " ".join(value.split())
