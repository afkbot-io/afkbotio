"""Contracts and errors for skill marketplace services."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SkillMarketplaceSourceStats:
    """Aggregated popularity/context metadata for one marketplace source."""

    installs_source: str = ""
    total_installs: int | None = None
    total_installs_display: str = ""
    repo_social_source: str = ""
    repo_stars: int | None = None
    repo_forks: int | None = None
    repo_watchers: int | None = None


@dataclass(frozen=True, slots=True)
class SkillMarketplaceListItem:
    """One skill item discovered from source listing."""

    name: str
    source: str
    path: str
    summary: str = ""
    canonical_source: str = ""
    rank: int | None = None
    installs: int | None = None
    installs_display: str = ""
    installed: bool = False
    installed_name: str | None = None
    installed_origin: str | None = None


@dataclass(frozen=True, slots=True)
class SkillMarketplaceListResult:
    """Marketplace browse/search result with source-level metadata."""

    source: str
    items: tuple[SkillMarketplaceListItem, ...]
    source_stats: SkillMarketplaceSourceStats = field(default_factory=SkillMarketplaceSourceStats)


@dataclass(frozen=True, slots=True)
class SkillMarketplaceInstallRecord:
    """Metadata for one installed profile skill."""

    name: str
    path: str
    source: str
    summary: str
    manifest_path: str | None = None
    available: bool = True
    missing_requirements: tuple[str, ...] = ()
    execution_mode: str = "advisory"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Normalized parsed marketplace source descriptor."""

    raw_source: str
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    base_path: str = ""
    direct_url: str | None = None
    skill_hint: str | None = None

    @property
    def is_direct(self) -> bool:
        """Return whether source resolves directly to one SKILL.md URL."""

        return self.direct_url is not None


class SkillMarketplaceError(RuntimeError):
    """Raised for deterministic marketplace install/list failures."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
