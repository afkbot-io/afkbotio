"""Pure helper functions for policy value extraction and normalization."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from urllib.parse import ParseResult, urlparse

from afkbot.services.policy.contracts import PolicyViolationError

PATH_PARAM_NAMES = {
    "path",
    "paths",
    "cwd",
    "dir",
    "dirs",
    "directory",
    "directories",
    "document",
    "file_path",
    "filepath",
    "photo",
    "input_path",
    "output_path",
    "root_path",
    "root_dir",
    "working_dir",
    "working_directory",
}
URL_PARAM_PARTS = ("url", "uri", "endpoint", "host", "domain")
SHELL_PARAM_NAMES = ("cmd", "command", "chars")
SHELL_SEGMENT_SPLIT_RE = re.compile(r"(?:\|\||&&|;|\||\n)")
SHELL_COMMAND_SUBSTITUTION_RE = re.compile(r"(?:\$\(|`)")
SCHEMELESS_NETWORK_COMMANDS = frozenset({"curl", "wget", "ping", "ssh"})
SSH_DIRECT_HOST_OPTIONS = frozenset({"-J", "-W"})
SSH_FORWARD_TARGET_OPTIONS = frozenset({"-L", "-R"})
SCHEMELESS_HOST_ARG_OPTIONS: dict[str, frozenset[str]] = {
    "curl": frozenset(
        {
            "-o",
            "--output",
            "-x",
            "--proxy",
            "-A",
            "--user-agent",
            "-u",
            "--user",
            "-H",
            "--header",
            "-d",
            "--data",
            "--data-raw",
            "--data-binary",
            "-e",
            "--referer",
            "--interface",
            "--connect-to",
            "--resolve",
            "--url",
            "--request",
            "-X",
            "--form",
            "-F",
            "--cookie",
            "-b",
            "--config",
            "-K",
            "--cacert",
            "--capath",
            "-E",
            "--cert",
            "--key",
            "--proto-redir",
            "--connect-timeout",
            "--max-time",
        }
    ),
    "wget": frozenset(
        {
            "-O",
            "--output-document",
            "-o",
            "--output-file",
            "--header",
            "--user",
            "--password",
            "--post-data",
            "--post-file",
            "--body-data",
            "--body-file",
            "--timeout",
            "-e",
            "--execute",
            "--referer",
            "--directory-prefix",
            "-P",
            "--input-file",
            "-i",
        }
    ),
    "ping": frozenset({"-c", "-i", "-W", "-w", "-t", "-s", "-I", "-m"}),
    "ssh": frozenset(
        {
            "-b",
            "-c",
            "-D",
            "-E",
            "-e",
            "-F",
            "-i",
            "-I",
            "-l",
            "-m",
            "-O",
            "-o",
            "-p",
            "-Q",
            "-S",
            "-w",
        }
    ),
}


def parse_string_set(*, raw: str, field_name: str) -> set[str]:
    """Parse JSON string lists from persisted profile policy fields."""

    if not raw.strip():
        return set()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyViolationError(reason=f"Profile policy {field_name} is invalid JSON list") from exc
    if not isinstance(data, list):
        raise PolicyViolationError(reason=f"Profile policy {field_name} is invalid JSON list")
    normalized: set[str] = set()
    for item in data:
        value = str(item).strip()
        if value:
            normalized.add(value)
    return normalized


def normalize_path(*, root_dir: Path | None, raw: str) -> Path:
    """Resolve relative paths under configured policy root when present."""

    path = Path(raw).expanduser()
    if not path.is_absolute():
        if root_dir is not None:
            path = root_dir / path
        else:
            path = Path.cwd() / path
    return path.resolve(strict=False)


def host_matches(*, host: str, allowed: str) -> bool:
    """Return whether concrete host matches allowlist entry or wildcard."""

    normalized_host = host.strip().lower()
    normalized_allowed = allowed.strip().lower()
    if not normalized_host or not normalized_allowed:
        return False
    if normalized_allowed == "*":
        return True
    return normalized_host == normalized_allowed or normalized_host.endswith(f".{normalized_allowed}")


def extract_path_values(value: object, *, field_name: str | None = None) -> list[str]:
    """Recursively collect path-like parameter values from nested tool payloads."""

    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend(extract_path_values(item, field_name=str(key)))
        return result
    if isinstance(value, list):
        list_result: list[str] = []
        for item in value:
            list_result.extend(extract_path_values(item, field_name=field_name))
        return list_result
    if isinstance(value, str) and field_name is not None:
        lowered = field_name.lower()
        if is_path_field_name(lowered):
            return [value]
    return []


def is_path_field_name(field_name: str) -> bool:
    """Return whether parameter name should be treated as filesystem path."""

    if field_name in PATH_PARAM_NAMES:
        return True
    return field_name.endswith("_path") or field_name.endswith("_dir") or field_name.endswith(
        "_directory"
    )


def extract_hosts(value: object, *, field_name: str | None = None) -> list[str]:
    """Recursively collect host targets from URL-like or shell parameters."""

    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend(extract_hosts(item, field_name=str(key)))
        return result
    if isinstance(value, list):
        list_result: list[str] = []
        for item in value:
            list_result.extend(extract_hosts(item, field_name=field_name))
        return list_result
    if isinstance(value, str) and field_name is not None:
        lowered = field_name.lower()
        if lowered in SHELL_PARAM_NAMES:
            return extract_hosts_from_shell_command(value)
        if not any(part in lowered for part in URL_PARAM_PARTS):
            return []
        parsed = safe_urlparse(value)
        if parsed is not None and parsed.hostname is not None:
            return [parsed.hostname]
        reparsed = safe_urlparse(f"//{value}")
        if reparsed is not None and reparsed.hostname is not None:
            return [reparsed.hostname]
    return []


def extract_hosts_from_shell_command(raw: str) -> list[str]:
    """Extract HTTP and scheme-less host targets from shell commands."""

    hosts: list[str] = []
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError:
        return hosts
    for token in tokens:
        parsed = safe_urlparse(token)
        if (
            parsed is not None
            and parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname is not None
        ):
            hosts.append(parsed.hostname)
    command_index = find_first_command_index(tokens)
    if command_index is None:
        return hosts
    command_name = Path(tokens[command_index]).name
    if command_name not in SCHEMELESS_NETWORK_COMMANDS:
        return hosts
    option_args = SCHEMELESS_HOST_ARG_OPTIONS.get(command_name, frozenset())
    pending_ssh_option: str | None = None
    skip_next = False
    for token in tokens[command_index + 1 :]:
        if pending_ssh_option is not None:
            hosts.extend(extract_hosts_from_ssh_option_value(option=pending_ssh_option, value=token))
            pending_ssh_option = None
            continue
        if skip_next:
            skip_next = False
            continue
        if command_name == "ssh":
            inline_hosts = extract_hosts_from_inline_ssh_option(token)
            if inline_hosts is not None:
                hosts.extend(inline_hosts)
                continue
            if token in SSH_DIRECT_HOST_OPTIONS or token in SSH_FORWARD_TARGET_OPTIONS:
                pending_ssh_option = token
                continue
        if token in option_args:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        hostname = extract_hostname_from_shell_token(token)
        if hostname is not None:
            hosts.append(hostname)
    return dedupe_preserve_order(hosts)


def extract_commands(value: object, *, field_name: str | None = None) -> list[str]:
    """Recursively collect primary shell command names from nested params."""

    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.extend(extract_commands(item, field_name=str(key)))
        return result
    if isinstance(value, list):
        list_result: list[str] = []
        for item in value:
            list_result.extend(extract_commands(item, field_name=field_name))
        return list_result
    if isinstance(value, str) and field_name is not None and field_name.lower() in SHELL_PARAM_NAMES:
        return extract_command_tokens(value)
    return []


def contains_command_substitution(value: object, *, field_name: str | None = None) -> bool:
    """Return whether shell payload uses command substitution forms."""

    if isinstance(value, dict):
        return any(
            contains_command_substitution(item, field_name=str(key))
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_command_substitution(item, field_name=field_name) for item in value)
    if isinstance(value, str) and field_name is not None and field_name.lower() in SHELL_PARAM_NAMES:
        return SHELL_COMMAND_SUBSTITUTION_RE.search(value) is not None
    return False


def extract_command_tokens(raw: str, *, depth: int = 0) -> list[str]:
    """Extract primary command tokens from one shell expression."""

    if depth > 3:
        return []
    normalized = raw.strip()
    if not normalized:
        return []

    commands: list[str] = []
    for segment in SHELL_SEGMENT_SPLIT_RE.split(normalized):
        chunk = segment.strip()
        if not chunk:
            continue
        token = first_command_token(chunk)
        if token:
            commands.append(token)
        commands.extend(extract_nested_shell_command(chunk, depth=depth + 1))
    return dedupe_preserve_order(commands)


def extract_nested_shell_command(raw: str, *, depth: int) -> list[str]:
    """Extract commands from `shell -c` wrapper forms."""

    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    if len(parts) < 3:
        return []
    shell_name = Path(parts[0]).name
    if shell_name not in {"sh", "bash", "zsh", "dash", "fish"}:
        return []
    for idx, token in enumerate(parts[:-1]):
        if token == "-c":
            nested = parts[idx + 1]
            return extract_command_tokens(nested, depth=depth)
    return []


def first_command_token(raw: str) -> str | None:
    """Return first executable token from shell segment after wrappers/env vars."""

    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return None
    index = find_first_command_index(parts)
    if index is None:
        return None
    return Path(parts[index]).name


def find_first_command_index(parts: list[str]) -> int | None:
    """Return index of executable token in shell argv."""

    if not parts:
        return None
    wrappers = {"env", "command", "sudo", "nohup", "time"}
    idx = 0
    while idx < len(parts):
        token = parts[idx]
        if token == "--":
            idx += 1
            continue
        if "=" in token and not token.startswith("="):
            name, _, _ = token.partition("=")
            if name.replace("_", "a").isalnum() and name[0].isalpha():
                idx += 1
                continue
        if token in wrappers:
            idx += 1
            continue
        if token.startswith("-") and idx > 0 and parts[idx - 1] in {"env", "command"}:
            idx += 1
            continue
        return idx
    return None


def extract_hostname_from_shell_token(token: str) -> str | None:
    """Extract hostname from raw shell token when it looks network-bound."""

    candidate = token.strip()
    if not candidate or candidate.startswith("@"):
        return None
    if "://" in candidate:
        parsed = safe_urlparse(candidate)
        return parsed.hostname if parsed is not None else None
    reparsed = safe_urlparse(f"//{candidate}")
    if reparsed is None:
        return None
    hostname = reparsed.hostname
    if hostname is None:
        return None
    if "." in hostname or ":" in candidate or hostname == "localhost":
        return hostname
    return None


def extract_hosts_from_inline_ssh_option(token: str) -> list[str] | None:
    """Extract hosts from inline SSH option forms like `-Jhost` or `-L8080:host:80`."""

    for option in SSH_DIRECT_HOST_OPTIONS | SSH_FORWARD_TARGET_OPTIONS:
        if not token.startswith(option) or len(token) == len(option):
            continue
        return extract_hosts_from_ssh_option_value(option=option, value=token[len(option) :])
    return None


def extract_hosts_from_ssh_option_value(*, option: str, value: str) -> list[str]:
    """Extract hosts from SSH option values that encode remote destinations."""

    if option == "-J":
        return extract_ssh_jump_hosts(value)
    if option == "-W":
        hostname = extract_hostname_from_shell_token(value)
    elif option in SSH_FORWARD_TARGET_OPTIONS:
        hostname = extract_ssh_forward_target_host(value)
    else:
        hostname = None
    return [hostname] if hostname is not None else []


def extract_ssh_jump_hosts(value: str) -> list[str]:
    """Extract every jump host from SSH `-J` option payloads."""

    hosts: list[str] = []
    for candidate in value.split(","):
        hostname = extract_hostname_from_shell_token(candidate)
        if hostname is not None:
            hosts.append(hostname)
    return hosts


def extract_ssh_forward_target_host(value: str) -> str | None:
    """Extract forwarded destination host from SSH `-L`/`-R` option payloads."""

    target = value.strip()
    if not target:
        return None
    parts = target.rsplit(":", 2)
    if len(parts) != 3:
        return None
    return extract_hostname_from_shell_token(parts[1])


def dedupe_preserve_order(values: list[str]) -> list[str]:
    """Remove duplicates while keeping first-seen order."""

    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def safe_urlparse(value: str) -> ParseResult | None:
    """Parse URL-like values while tolerating malformed bracket tokens."""

    try:
        return urlparse(value)
    except ValueError:
        return None
