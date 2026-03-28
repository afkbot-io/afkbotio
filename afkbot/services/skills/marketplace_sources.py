"""Source parsing and path resolution for skill marketplace imports."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from afkbot.services.naming import normalize_runtime_name
from afkbot.services.skills.marketplace_contracts import SkillMarketplaceError, SourceDescriptor

_SOURCE_SPEC_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class MarketplaceSourceResolver:
    """Parse marketplace sources and derive candidate GitHub/raw URLs."""

    def __init__(
        self,
        *,
        skills_sh_hosts: frozenset[str],
        github_hosts: frozenset[str],
        raw_github_hosts: frozenset[str],
        default_ref_candidates: tuple[str, ...],
        default_skill_base_paths: tuple[str, ...],
    ) -> None:
        self._skills_sh_hosts = skills_sh_hosts
        self._github_hosts = github_hosts
        self._raw_github_hosts = raw_github_hosts
        self._default_ref_candidates = default_ref_candidates
        self._default_skill_base_paths = default_skill_base_paths

    def parse_source(self, raw_source: str) -> SourceDescriptor:
        """Parse one marketplace source string into a normalized descriptor."""

        source = raw_source.strip()
        if not source:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source is required",
            )

        if source.startswith("skills.sh/"):
            source = f"https://{source}"

        if _SOURCE_SPEC_RE.fullmatch(source):
            owner, repo = source.split("/", 1)
            return SourceDescriptor(
                raw_source=raw_source.strip(),
                owner=self.validate_repo_segment(owner),
                repo=self.normalize_repo_name(repo),
            )

        parsed = urlparse(source)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason=f"Unsupported source: {raw_source}",
            )

        host = (parsed.hostname or "").lower()
        segments = self.parse_path_segments(parsed.path)

        if host in self._skills_sh_hosts:
            return self.parse_skills_sh_source(raw_source.strip(), segments)
        if host in self._github_hosts:
            return self.parse_github_source(raw_source.strip(), segments)
        if host in self._raw_github_hosts:
            return self.parse_raw_github_source(raw_source.strip(), segments)

        raise SkillMarketplaceError(
            error_code="skill_marketplace_invalid_source",
            reason=f"Unsupported source host: {host or 'unknown'}",
        )

    def candidate_refs(self, ref: str | None) -> tuple[str, ...]:
        """Return candidate git refs to try for repository sources."""

        if ref is not None and ref.strip():
            return (ref.strip(),)
        return self._default_ref_candidates

    def candidate_skill_urls(
        self,
        *,
        owner: str,
        repo: str,
        requested_skill: str,
        ref: str | None,
        base_path: str,
    ) -> tuple[str, ...]:
        """Build candidate raw GitHub URLs for one requested skill name."""

        refs = self.candidate_refs(ref)
        candidate_dirs: list[str] = []

        if base_path:
            normalized_base = self.normalize_repo_path(base_path)
            candidate_dirs.append(normalized_base)
            if not normalized_base.endswith(f"/{requested_skill}") and normalized_base != requested_skill:
                candidate_dirs.append(f"{normalized_base}/{requested_skill}")
        else:
            for prefix in self._default_skill_base_paths:
                if prefix:
                    candidate_dirs.append(f"{prefix}/{requested_skill}")
                else:
                    candidate_dirs.append(requested_skill)

        dedup_dirs = tuple(dict.fromkeys(path.strip("/") for path in candidate_dirs if path.strip("/")))
        urls: list[str] = []
        for current_ref in refs:
            for directory in dedup_dirs:
                urls.append(self.build_raw_github_url(owner, repo, current_ref, f"{directory}/SKILL.md"))
        return tuple(urls)

    def extract_skill_names_from_tree(
        self,
        *,
        payload: dict[str, object],
        base_path: str,
    ) -> list[tuple[str, str]]:
        """Extract marketplace-visible skill names from a GitHub tree payload."""

        raw_tree = payload.get("tree")
        if not isinstance(raw_tree, list):
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source tree payload is invalid",
            )

        normalized_base = self.normalize_repo_path(base_path) if base_path else ""
        result: dict[str, str] = {}
        for raw_item in raw_tree:
            if not isinstance(raw_item, dict):
                continue
            raw_type = raw_item.get("type")
            raw_path = raw_item.get("path")
            if raw_type != "blob" or not isinstance(raw_path, str):
                continue
            if not raw_path.endswith("/SKILL.md"):
                continue

            skill_path = raw_path[: -len("/SKILL.md")]
            if normalized_base:
                match = self.match_skill_under_base(skill_path=skill_path, base_path=normalized_base)
                if match is None:
                    continue
                name, rel = match
                result.setdefault(name, rel)
                continue

            match = self.match_skill_under_default_layout(skill_path)
            if match is None:
                continue
            name, rel = match
            result.setdefault(name, rel)

        return sorted(result.items(), key=lambda item: item[0])

    def parse_skills_sh_source(
        self,
        raw_source: str,
        segments: list[str],
    ) -> SourceDescriptor:
        """Parse skills.sh-style source URL."""

        if len(segments) < 2:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="skills.sh source must include owner/repo",
            )
        owner = self.validate_repo_segment(segments[0])
        repo = self.normalize_repo_name(segments[1])
        skill_hint = self.normalize_optional_skill_name(segments[2] if len(segments) > 2 else None)
        return SourceDescriptor(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            skill_hint=skill_hint,
        )

    def parse_github_source(
        self,
        raw_source: str,
        segments: list[str],
    ) -> SourceDescriptor:
        """Parse GitHub web URL source."""

        if len(segments) < 2:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="GitHub source must include owner/repo",
            )

        owner = self.validate_repo_segment(segments[0])
        repo = self.normalize_repo_name(segments[1])
        if len(segments) >= 5 and segments[2] in {"tree", "blob"}:
            ref = segments[3].strip()
            if not ref:
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_invalid_source",
                    reason="GitHub source ref is required",
                )
            path = self.normalize_repo_path("/".join(segments[4:]))
            if path.endswith("/SKILL.md"):
                return SourceDescriptor(
                    raw_source=raw_source,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    direct_url=self.build_raw_github_url(owner, repo, ref, path),
                    skill_hint=self.normalize_optional_skill_name(path.split("/")[-2]),
                )
            return SourceDescriptor(
                raw_source=raw_source,
                owner=owner,
                repo=repo,
                ref=ref,
                base_path=path,
                skill_hint=self.normalize_optional_skill_name(path.split("/")[-1]),
            )

        return SourceDescriptor(raw_source=raw_source, owner=owner, repo=repo)

    def parse_raw_github_source(
        self,
        raw_source: str,
        segments: list[str],
    ) -> SourceDescriptor:
        """Parse raw.githubusercontent.com source URL."""

        if len(segments) < 4:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Raw GitHub source must include owner/repo/ref/path",
            )

        owner = self.validate_repo_segment(segments[0])
        repo = self.normalize_repo_name(segments[1])
        ref = segments[2].strip()
        if not ref:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Raw GitHub ref is required",
            )
        path = self.normalize_repo_path("/".join(segments[3:]))
        if not path.endswith("/SKILL.md"):
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Raw GitHub source must point to SKILL.md",
            )

        return SourceDescriptor(
            raw_source=raw_source,
            owner=owner,
            repo=repo,
            ref=ref,
            direct_url=self.build_raw_github_url(owner, repo, ref, path),
            skill_hint=self.normalize_optional_skill_name(path.split("/")[-2]),
        )

    @staticmethod
    def parse_path_segments(path: str) -> list[str]:
        """Split URL path into decoded, non-empty segments."""

        return [segment for segment in (unquote(part).strip() for part in path.split("/")) if segment]

    @staticmethod
    def validate_repo_segment(value: str) -> str:
        """Validate one owner/path segment."""

        candidate = value.strip()
        if not _SAFE_SEGMENT_RE.fullmatch(candidate):
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason=f"Invalid repository segment: {value}",
            )
        return candidate

    @staticmethod
    def normalize_repo_name(value: str) -> str:
        """Normalize repository name, stripping trailing .git."""

        candidate = value.strip()
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        if not _SAFE_SEGMENT_RE.fullmatch(candidate):
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason=f"Invalid repository name: {value}",
            )
        return candidate

    @staticmethod
    def normalize_repo_path(path: str) -> str:
        """Normalize one repository-relative path without traversal segments."""

        parts = [part.strip() for part in path.split("/") if part.strip()]
        if not parts:
            return ""
        normalized_parts: list[str] = []
        for part in parts:
            if part in {".", ".."}:
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_invalid_source",
                    reason=f"Invalid source path: {path}",
                )
            normalized_parts.append(part)
        return "/".join(normalized_parts)

    @staticmethod
    def normalize_optional_skill_name(value: str | None) -> str | None:
        """Normalize optional skill name, returning None when invalid."""

        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return normalize_runtime_name(stripped)
        except ValueError:
            return None

    def match_skill_under_base(self, *, skill_path: str, base_path: str) -> tuple[str, str] | None:
        """Match a skill tree path under one explicit base path."""

        if skill_path == base_path:
            candidate_name = skill_path.rsplit("/", 1)[-1]
            normalized = self.normalize_optional_skill_name(candidate_name)
            if normalized is None:
                return None
            return normalized, f"{skill_path}/SKILL.md"

        prefix = f"{base_path}/"
        if not skill_path.startswith(prefix):
            return None
        relative = skill_path[len(prefix) :]
        if "/" in relative:
            return None
        normalized = self.normalize_optional_skill_name(relative)
        if normalized is None:
            return None
        return normalized, f"{skill_path}/SKILL.md"

    def match_skill_under_default_layout(self, skill_path: str) -> tuple[str, str] | None:
        """Match skill tree path under default `skills/...` layouts."""

        parts = [part for part in skill_path.split("/") if part]
        candidate_name: str | None = None
        if len(parts) == 1:
            candidate_name = parts[0]
        elif len(parts) == 2 and parts[0] == "skills":
            candidate_name = parts[1]
        elif len(parts) == 3 and parts[0] == "skills" and parts[1] == ".curated":
            candidate_name = parts[2]

        if candidate_name is None:
            return None
        normalized = self.normalize_optional_skill_name(candidate_name)
        if normalized is None:
            return None
        return normalized, f"{skill_path}/SKILL.md"

    @staticmethod
    def build_raw_github_url(owner: str, repo: str, ref: str, path: str) -> str:
        """Build raw GitHub URL from repo coordinates and a path."""

        normalized_path = "/".join(part for part in path.split("/") if part)
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{normalized_path}"
