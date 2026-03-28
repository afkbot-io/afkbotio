"""Heuristics for deriving AFKBOT skill manifests from marketplace/source markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.skills.markdown import FrontmatterValue

_CODE_BLOCK_RE = re.compile(r"```(?P<lang>[\w+-]+)?\n(?P<body>.*?)```", re.DOTALL)
_PIP_INSTALL_RE = re.compile(
    r"(?:uv\s+pip\s+install|python(?:3)?\s+-m\s+pip\s+install)\s+([^\n`#]+)",
    re.IGNORECASE,
)
_SCRIPT_RE = re.compile(r"(?<!\w)(?:python(?:3)?\s+)?scripts/[A-Za-z0-9_./-]+")
_FILE_EXTENSION_RE = re.compile(r"\.(?:docx|pdf|xlsx|csv|json|ya?ml|txt|md)\b", re.IGNORECASE)
_BINARY_DOCUMENT_HINT_RE = re.compile(r"\b(?:docx|pdf)\b", re.IGNORECASE)
_FILE_VERB_RE = re.compile(
    r"\b(?:read|review|create|edit|update|modify|write|open|extract|render|convert|созда|редакт|прочита|откро|извлеч|рендер)\w*\b",
    re.IGNORECASE,
)
_PATH_SIGNAL_RE = re.compile(r"\b(?:output|tmp|temp|artifacts?)/", re.IGNORECASE)
_EDIT_VERB_RE = re.compile(r"\b(?:edit|update|modify|replace|редакт|замен|обнов)\w*\b", re.IGNORECASE)
_COMMAND_FIRST_TOKEN_RE = re.compile(r"^\s*([a-zA-Z0-9_.-]+)\b")
_IGNORED_COMMANDS = frozenset(
    {
        "uv",
        "python",
        "python3",
        "bash",
        "sh",
        "sudo",
        "brew",
        "apt",
        "apt-get",
        "dnf",
        "yum",
        "echo",
        "cat",
        "cd",
    }
)
_SHELL_LANGS = frozenset({"", "sh", "shell", "bash", "zsh", "console", "terminal"})
_MARKDOWNISH_LINE_RE = re.compile(
    r"^(?:[#>*-]\s|[A-Za-z0-9_.-]+:\s|[A-ZА-Я][^`]*\s+[A-Za-zА-Яа-я])"
)


@dataclass(frozen=True, slots=True)
class InferredManifestHints:
    """Machine-derived hints from skill markdown body."""

    tool_names: tuple[str, ...] = ()
    preferred_tool_order: tuple[str, ...] = ()
    suggested_bins: tuple[str, ...] = ()
    requires_python_packages: tuple[str, ...] = ()


def infer_manifest_hints(
    *,
    content: str,
    metadata: dict[str, FrontmatterValue],
) -> InferredManifestHints:
    """Infer executable surface and requirements from markdown content."""

    del metadata
    lowered = content.lower()
    tools = _infer_tool_names(content=content, lowered=lowered)
    preferred = _infer_preferred_tool_order(tool_names=tools, lowered=lowered)
    requires_python_packages = _infer_python_packages(content)
    suggested_bins = _infer_bins(content)
    return InferredManifestHints(
        tool_names=tools,
        preferred_tool_order=preferred,
        suggested_bins=suggested_bins,
        requires_python_packages=requires_python_packages,
    )


def _infer_tool_names(*, content: str, lowered: str) -> tuple[str, ...]:
    tools: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        normalized = name.strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        tools.append(normalized)

    binary_document_signal = (
        ".docx" in lowered
        or ".pdf" in lowered
        or _BINARY_DOCUMENT_HINT_RE.search(content) is not None
    )
    file_signal = (
        _FILE_EXTENSION_RE.search(content) is not None
        or _PATH_SIGNAL_RE.search(lowered) is not None
        or (_FILE_VERB_RE.search(lowered) is not None and "file" in lowered)
        or "document" in lowered
        or "docx" in lowered
        or "pdf" in lowered
    )
    if binary_document_signal:
        add("bash.exec")
        add("file.list")
        return tuple(tools)
    if file_signal:
        add("file.read")
        add("file.write")
        add("file.list")
        if _EDIT_VERB_RE.search(lowered) is not None:
            add("file.edit")

    if _contains_shell_workflow(content):
        add("bash.exec")

    return tuple(tools)


def _infer_preferred_tool_order(*, tool_names: tuple[str, ...], lowered: str) -> tuple[str, ...]:
    if not tool_names:
        return ()
    if ".docx" in lowered or ".pdf" in lowered or _BINARY_DOCUMENT_HINT_RE.search(lowered) is not None:
        preferred_order = (
            "bash.exec",
            "file.read",
            "file.list",
            "file.write",
            "file.edit",
        )
    else:
        preferred_order = (
            "file.read",
            "file.list",
            "bash.exec",
            "file.write",
            "file.edit",
        )
    seen = set(tool_names)
    ordered = [name for name in preferred_order if name in seen]
    for name in tool_names:
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _infer_python_packages(content: str) -> tuple[str, ...]:
    packages: list[str] = []
    seen: set[str] = set()
    for match in _PIP_INSTALL_RE.finditer(content):
        for token in match.group(1).split():
            candidate = token.strip()
            if (
                not candidate
                or candidate.startswith("-")
                or "/" in candidate
                or candidate in seen
            ):
                continue
            seen.add(candidate)
            packages.append(candidate)
    return tuple(packages)


def _infer_bins(content: str) -> tuple[str, ...]:
    bins: list[str] = []
    seen: set[str] = set()

    for lang, block in _iter_shell_code_blocks(content):
        del lang
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if _looks_like_non_shell_line(line):
                continue
            token_match = _COMMAND_FIRST_TOKEN_RE.match(line)
            if token_match is None:
                continue
            command = token_match.group(1)
            if command in _IGNORED_COMMANDS or command in seen:
                continue
            if command.isdigit():
                continue
            seen.add(command)
            bins.append(command)

    return tuple(bins)


def _contains_shell_workflow(content: str) -> bool:
    if _SCRIPT_RE.search(content) is not None:
        return True
    for _lang, block in _iter_shell_code_blocks(content):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _looks_like_non_shell_line(stripped):
                continue
            if _COMMAND_FIRST_TOKEN_RE.match(stripped) is None:
                continue
            return True
    return False


def _iter_shell_code_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in _CODE_BLOCK_RE.finditer(content):
        lang = str(match.group("lang") or "").strip().lower()
        body = str(match.group("body") or "")
        if lang not in _SHELL_LANGS:
            continue
        blocks.append((lang, body))
    return blocks


def _looks_like_non_shell_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _MARKDOWNISH_LINE_RE.match(stripped):
        return True
    token_match = _COMMAND_FIRST_TOKEN_RE.match(stripped)
    if token_match is None:
        return True
    command = token_match.group(1)
    if stripped.startswith(f"{command}:"):
        return True
    if command[:1].isupper() and " " in stripped:
        return True
    return False
