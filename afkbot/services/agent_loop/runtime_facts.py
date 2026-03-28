"""Trusted local runtime facts for prompt context."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil

from afkbot.services.tools.workspace import resolve_tool_workspace_base_dir
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class TrustedRuntimeFacts:
    """Compact trusted facts about the current AFKBOT execution environment."""

    workspace_root: Path
    repo_root: Path | None
    execution_target: str
    current_host_scope: str
    os_name: str
    distro: str | None
    arch: str
    shell_path: str | None
    is_root: bool | None
    has_sudo: bool
    has_systemctl: bool
    package_managers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ProcessRuntimeFacts:
    """Process-stable trusted host facts that can be reused across turns."""

    execution_target: str
    current_host_scope: str
    os_name: str
    distro: str | None
    arch: str
    shell_path: str | None
    is_root: bool | None
    has_sudo: bool
    has_systemctl: bool
    package_managers: tuple[str, ...]


class TrustedRuntimeFactsService:
    """Collect and render trusted local runtime facts for one turn."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._process_facts: _ProcessRuntimeFacts | None = None

    async def build_prompt_block(self, *, profile_id: str) -> str:
        """Return prompt-ready trusted runtime facts for the active profile."""

        facts = self._collect_facts(profile_id=profile_id)
        package_managers = ", ".join(facts.package_managers) if facts.package_managers else "none detected"
        shell_line = facts.shell_path or "unknown"
        repo_root = str(facts.repo_root) if facts.repo_root is not None else "not detected"
        distro_line = facts.distro or "unknown"
        if facts.is_root is None:
            root_line = "unknown"
        else:
            root_line = "yes" if facts.is_root else "no"
        sudo_line = "yes" if facts.has_sudo else "no"
        systemctl_line = "yes" if facts.has_systemctl else "no"
        return "\n".join(
            [
                "Trusted runtime facts for this AFKBOT process.",
                "Treat this block as authoritative local environment context.",
                f"- workspace_root: {facts.workspace_root}",
                f"- repo_root: {repo_root}",
                f"- execution_target: {facts.execution_target}",
                f"- current_host_scope: {facts.current_host_scope}",
                f"- os: {facts.os_name}",
                f"- distro: {distro_line}",
                f"- arch: {facts.arch}",
                f"- shell: {shell_line}",
                f"- is_root: {root_line}",
                f"- has_sudo: {sudo_line}",
                f"- has_systemctl: {systemctl_line}",
                f"- package_managers: {package_managers}",
                "- This session is already a valid execution environment for the current host and workspace above.",
                "- For system or package-management tasks, trust these facts before guessing.",
                "- If shell or file tools are visible and policy allows, execute current-host tasks here instead of turning them into manual instructions.",
                "- Another host or service mentioned by the user is not a blocker by itself. Inspect what this environment can actually reach before concluding that extra access is required.",
                "- If a required fact is missing, inspect with safe shell commands before mutating the system.",
            ]
        )

    def _collect_facts(self, *, profile_id: str) -> TrustedRuntimeFacts:
        workspace_root = resolve_tool_workspace_base_dir(
            settings=self._settings,
            profile_id=profile_id,
        ).resolve(strict=False)
        repo_root = self._resolve_repo_root(start_path=workspace_root)
        process_facts = self._get_process_facts()
        return TrustedRuntimeFacts(
            workspace_root=workspace_root,
            repo_root=repo_root,
            execution_target=process_facts.execution_target,
            current_host_scope=process_facts.current_host_scope,
            os_name=process_facts.os_name,
            distro=process_facts.distro,
            arch=process_facts.arch,
            shell_path=process_facts.shell_path,
            is_root=process_facts.is_root,
            has_sudo=process_facts.has_sudo,
            has_systemctl=process_facts.has_systemctl,
            package_managers=process_facts.package_managers,
        )

    def _get_process_facts(self) -> _ProcessRuntimeFacts:
        """Return process-stable trusted facts, computing them only once per service instance."""

        if self._process_facts is None:
            os_name = platform.system().strip().lower() or "unknown"
            distro = self._read_linux_distro() if os_name == "linux" else None
            package_managers = tuple(
                name for name in ("apt", "apt-get", "dnf", "yum", "brew", "pacman")
                if shutil.which(name) is not None
            )
            self._process_facts = _ProcessRuntimeFacts(
                execution_target="local_runtime",
                current_host_scope="shell and file actions apply to this current AFKBOT runtime host and allowed workspace",
                os_name=os_name,
                distro=distro,
                arch=platform.machine().strip().lower() or "unknown",
                shell_path=self._normalize_shell_path(os.environ.get("SHELL")),
                is_root=self._detect_is_root(),
                has_sudo=shutil.which("sudo") is not None,
                has_systemctl=shutil.which("systemctl") is not None,
                package_managers=package_managers,
            )
        return self._process_facts

    def _resolve_repo_root(self, *, start_path: Path) -> Path | None:
        candidates = [start_path, self._settings.root_dir.resolve(strict=False)]
        seen: set[Path] = set()
        for candidate in candidates:
            current = candidate
            while True:
                if current in seen:
                    break
                seen.add(current)
                if (current / ".git").exists():
                    return current
                parent = current.parent
                if parent == current:
                    break
                current = parent
        return None

    @staticmethod
    def _normalize_shell_path(raw_value: str | None) -> str | None:
        normalized = str(raw_value or "").strip()
        return normalized or None

    @staticmethod
    def _detect_is_root() -> bool | None:
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            return None
        try:
            return bool(geteuid() == 0)
        except OSError:
            return None

    @staticmethod
    def _read_linux_distro() -> str | None:
        os_release_path = Path("/etc/os-release")
        if not os_release_path.exists():
            return None
        try:
            raw_lines = os_release_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        values: dict[str, str] = {}
        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line or "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip().strip('"').strip("'")
        for key in ("PRETTY_NAME", "ID", "NAME"):
            candidate = values.get(key, "").strip()
            if candidate:
                return candidate
        return None
