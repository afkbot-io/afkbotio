"""Inspection helpers for skill manifest and availability health."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from afkbot.services.skills.skills import SkillInfo, SkillLoader
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class SkillDoctorRecord:
    """Normalized skill health report for one visible skill."""

    name: str
    origin: str
    path: str
    available: bool
    execution_mode: str
    manifest_path: str | None
    manifest_valid: bool
    missing_requirements: tuple[str, ...]
    missing_suggested_requirements: tuple[str, ...]
    tool_names: tuple[str, ...]
    app_names: tuple[str, ...]
    preferred_tool_order: tuple[str, ...]
    suggested_bins: tuple[str, ...]
    install_hints: tuple[str, ...]
    repair_commands: tuple[str, ...]
    issues: tuple[str, ...]


class SkillDoctorService:
    """Build deterministic health summaries for loaded skills."""

    def __init__(self, settings: Settings, loader: SkillLoader | None = None) -> None:
        self._settings = settings
        self._loader = loader or SkillLoader(settings)

    async def inspect_profile(self, *, profile_id: str) -> list[SkillDoctorRecord]:
        """Inspect all skills visible to one profile."""

        items = await self._loader.list_skills(profile_id)
        return [self._record_from_skill(item, profile_id=profile_id) for item in items]

    def _record_from_skill(self, item: SkillInfo, *, profile_id: str) -> SkillDoctorRecord:
        issues: list[str] = []
        if item.manifest_path is None:
            issues.append("missing_manifest")
        if not item.manifest_valid:
            issues.extend(f"manifest:{issue}" for issue in item.manifest_errors)
        if item.manifest.execution_mode in {"executable", "dispatch"} and not (
            item.manifest.tool_names or item.manifest.app_names
        ):
            issues.append("missing_surface")
        if item.manifest.execution_mode == "dispatch" and not item.manifest.app_names:
            issues.append("dispatch_missing_app")
        if item.manifest.execution_mode == "advisory" and (
            item.manifest.tool_names or item.manifest.app_names
        ):
            issues.append("advisory_with_surface")
        if not item.available:
            issues.extend(f"availability:{issue}" for issue in item.missing_requirements)
        if item.missing_suggested_requirements:
            issues.extend(f"suggested:{issue}" for issue in item.missing_suggested_requirements)
        install_hints = suggest_skill_install_hints(item)
        repair_commands = suggest_skill_repair_commands(item, profile_id=profile_id)
        return SkillDoctorRecord(
            name=item.name,
            origin=item.origin,
            path=self._to_relative(item.path),
            available=item.available,
            execution_mode=item.manifest.execution_mode,
            manifest_path=None if item.manifest_path is None else self._to_relative(item.manifest_path),
            manifest_valid=item.manifest_valid,
            missing_requirements=item.missing_requirements,
            missing_suggested_requirements=item.missing_suggested_requirements,
            tool_names=item.manifest.tool_names,
            app_names=item.manifest.app_names,
            preferred_tool_order=item.manifest.preferred_tool_order,
            suggested_bins=item.manifest.suggested_bins,
            install_hints=install_hints,
            repair_commands=repair_commands,
            issues=tuple(issues),
        )

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())


def get_skill_doctor_service(settings: Settings) -> SkillDoctorService:
    """Return uncached skill doctor service."""

    return SkillDoctorService(settings=settings)


def suggest_skill_install_hints(item: SkillInfo) -> tuple[str, ...]:
    """Return actionable install hints for one unavailable skill."""

    python_packages = sorted(
        requirement.removeprefix("python:")
        for requirement in item.missing_requirements
        if requirement.startswith("python:")
    )
    missing_bins = sorted(
        requirement.removeprefix("bin:")
        for requirement in (*item.missing_requirements, *item.missing_suggested_requirements)
        if requirement.startswith("bin:")
    )

    hints: list[str] = []
    seen_hints: set[str] = set()

    def add_hint(hint: str) -> None:
        normalized = hint.strip()
        if not normalized or normalized in seen_hints:
            return
        seen_hints.add(normalized)
        hints.append(normalized)

    if python_packages:
        add_hint(f"uv pip install {' '.join(python_packages)}")

    remaining_bins: list[str] = []
    for binary in missing_bins:
        custom_hints = _custom_install_hints_for_bin(binary)
        if custom_hints:
            for hint in custom_hints:
                add_hint(hint)
            continue
        remaining_bins.append(binary)

    install_packages = _system_install_packages(remaining_bins)
    if install_packages:
        if sys.platform == "darwin":
            add_hint(f"brew install {' '.join(install_packages)}")
        elif sys.platform.startswith("linux"):
            add_hint(f"sudo apt-get install -y {' '.join(install_packages)}")
        else:
            add_hint(f"Install system packages or binaries: {', '.join(install_packages)}")
    return tuple(hints)


def suggest_skill_repair_commands(item: SkillInfo, *, profile_id: str) -> tuple[str, ...]:
    """Return operator commands that can repair one skill definition."""

    commands: list[str] = []
    if item.origin == "profile" and (item.manifest_path is None or not item.manifest_valid):
        commands.append(f"afk skill normalize --profile {profile_id} {item.name}")
    if not item.available:
        commands.append(f"afk skill doctor --profile {profile_id}")
    return tuple(commands)


def _system_install_packages(missing_bins: list[str]) -> tuple[str, ...]:
    package_names: list[str] = []
    seen: set[str] = set()
    for binary in missing_bins:
        package = _SYSTEM_PACKAGE_BY_BIN.get(binary, binary)
        if package in seen:
            continue
        seen.add(package)
        package_names.append(package)
    return tuple(package_names)


_SYSTEM_PACKAGE_BY_BIN: dict[str, str] = {
    "soffice": "libreoffice",
    "pdftoppm": "poppler" if sys.platform == "darwin" else "poppler-utils",
}

def _custom_install_hints_for_bin(binary: str) -> tuple[str, ...]:
    """Return platform-aware custom install hints for known external agent CLIs."""

    hints: list[str] = []

    def add(hint: str) -> None:
        normalized = hint.strip()
        if normalized and normalized not in hints:
            hints.append(normalized)

    if binary == "aider":
        add("uv tool install --force --python python3.12 --with pip aider-chat@latest")
        add("pipx install aider-chat")
        if not sys.platform.startswith("win"):
            add("curl -LsSf https://aider.chat/install.sh | sh")
        return tuple(hints)

    if binary == "claude":
        if sys.platform.startswith("win"):
            add("winget install Anthropic.ClaudeCode")
            return tuple(hints)
        add("curl -fsSL https://claude.ai/install.sh | bash")
        if sys.platform == "darwin":
            add("brew install --cask claude-code")
        return tuple(hints)

    if binary == "codex":
        add("npm install -g @openai/codex")
        if sys.platform == "darwin":
            add("brew install --cask codex")
        return tuple(hints)

    if binary == "gemini":
        add("npm install -g @google/gemini-cli")
        if sys.platform == "darwin":
            add("brew install gemini-cli")
        return tuple(hints)

    return ()
