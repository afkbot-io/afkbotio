"""Popularity/ranking helpers for marketplace listing results."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
import re
from urllib.parse import urlparse

from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceListItem,
    SkillMarketplaceError,
    SkillMarketplaceSourceStats,
    SourceDescriptor,
)
from afkbot.services.skills.marketplace_fetch import MarketplaceContentFetcher

_COMPACT_COUNT_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMB])?\s*$", re.IGNORECASE)
_TOTAL_INSTALLS_RE = re.compile(
    r">(?:\s|<!-- -->)*([0-9]+(?:\.[0-9]+)?\s*[KMB]?)(?:\s|<!-- -->)*total installs<",
    re.IGNORECASE,
)


class MarketplacePopularityResolver:
    """Resolve install and repo popularity signals for marketplace listings."""

    def __init__(
        self,
        *,
        fetcher: MarketplaceContentFetcher,
        default_skills_sh_host: str,
    ) -> None:
        self._fetcher = fetcher
        self._default_skills_sh_host = default_skills_sh_host

    def enrich_listing(
        self,
        *,
        descriptor: SourceDescriptor,
        items: list[SkillMarketplaceListItem],
    ) -> tuple[list[SkillMarketplaceListItem], SkillMarketplaceSourceStats]:
        """Return items enriched with popularity data and source-level stats."""

        if not items:
            return [], SkillMarketplaceSourceStats()

        source_stats = SkillMarketplaceSourceStats()
        enriched = list(items)
        if descriptor.owner and descriptor.repo:
            source_stats = self._resolve_github_repo_stats(owner=descriptor.owner, repo=descriptor.repo)
            skills_sh_items, total_installs, total_installs_display = self._resolve_skills_sh_installs(
                descriptor=descriptor,
            )
            if skills_sh_items or total_installs is not None or total_installs_display:
                source_stats = replace(
                    source_stats,
                    installs_source="skills.sh",
                    total_installs=total_installs,
                    total_installs_display=total_installs_display,
                )
            if skills_sh_items:
                enriched = self._merge_install_stats(items=enriched, installs_by_name=skills_sh_items)
        return enriched, source_stats

    def _resolve_skills_sh_installs(
        self,
        *,
        descriptor: SourceDescriptor,
    ) -> tuple[dict[str, tuple[int, str]], int | None, str]:
        if descriptor.owner is None or descriptor.repo is None:
            return {}, None, ""
        page_url = self._build_skills_sh_repo_url(descriptor)
        try:
            html = self._fetcher.fetch_html(page_url)
        except SkillMarketplaceError:  # pragma: no cover - metrics fallback
            return {}, None, ""

        installs_by_name = _parse_skills_sh_installs(
            html=html,
            owner=descriptor.owner,
            repo=descriptor.repo,
        )
        total_match = _TOTAL_INSTALLS_RE.search(html)
        if total_match is None:
            return installs_by_name, None, ""
        total_installs_display = " ".join(total_match.group(1).split())
        return installs_by_name, parse_compact_count(total_installs_display), total_installs_display

    def _resolve_github_repo_stats(self, *, owner: str, repo: str) -> SkillMarketplaceSourceStats:
        try:
            payload = self._fetcher.fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
        except SkillMarketplaceError:  # pragma: no cover - metrics fallback
            return SkillMarketplaceSourceStats()

        return SkillMarketplaceSourceStats(
            repo_social_source="github",
            repo_stars=_coerce_int(payload.get("stargazers_count")),
            repo_forks=_coerce_int(payload.get("forks_count")),
            repo_watchers=_coerce_int(payload.get("subscribers_count")),
        )

    def _build_skills_sh_repo_url(self, descriptor: SourceDescriptor) -> str:
        source = descriptor.raw_source.strip()
        if source.startswith("skills.sh/"):
            return f"https://{source}"

        parsed = urlparse(source)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
            host = parsed.netloc
            if "skills.sh" in parsed.hostname:
                return f"{parsed.scheme}://{host}/{descriptor.owner}/{descriptor.repo}"

        if descriptor.owner is None or descriptor.repo is None:
            return f"https://{self._default_skills_sh_host}"
        return f"https://{self._default_skills_sh_host}/{descriptor.owner}/{descriptor.repo}"

    @staticmethod
    def _merge_install_stats(
        *,
        items: list[SkillMarketplaceListItem],
        installs_by_name: dict[str, tuple[int, str]],
    ) -> list[SkillMarketplaceListItem]:
        enriched: list[SkillMarketplaceListItem] = []
        for item in items:
            installs, installs_display = installs_by_name.get(item.name, (None, ""))
            enriched.append(
                replace(
                    item,
                    installs=installs,
                    installs_display=installs_display,
                )
            )
        if any(item.installs is not None for item in enriched):
            enriched.sort(
                key=lambda item: (
                    item.installs is None,
                    -(item.installs if item.installs is not None else 0),
                    item.name,
                )
            )
            enriched = [
                replace(item, rank=index)
                for index, item in enumerate(enriched, start=1)
            ]
        return enriched


class _SkillsShRepoParser(HTMLParser):
    """Extract per-skill install counters from one `skills.sh/<owner>/<repo>` page."""

    def __init__(self, *, owner: str, repo: str) -> None:
        super().__init__(convert_charrefs=True)
        self._owner = owner
        self._repo = repo
        self._in_script = False
        self._current_slug: str | None = None
        self._current_name_parts: list[str] = []
        self._current_install_parts: list[str] = []
        self._capture_h3 = False
        self._capture_install_span = False
        self.results: dict[str, tuple[int, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "script":
            self._in_script = True
            return
        if self._in_script:
            return
        if tag == "a":
            href = (attr_map.get("href") or "").strip()
            slug = self._match_skill_href(href)
            if slug is not None:
                self._current_slug = slug
                self._current_name_parts = []
                self._current_install_parts = []
        elif self._current_slug is not None and tag == "h3":
            self._capture_h3 = True
        elif self._current_slug is not None and tag == "span":
            class_name = attr_map.get("class") or ""
            if "font-mono" in class_name and "text-sm" in class_name and "text-foreground" in class_name:
                self._capture_install_span = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
            return
        if self._in_script:
            return
        if tag == "h3":
            self._capture_h3 = False
        elif tag == "span":
            self._capture_install_span = False
        elif tag == "a" and self._current_slug is not None:
            installs_display = " ".join(part for part in self._current_install_parts if part).strip()
            installs = parse_compact_count(installs_display)
            if installs is not None:
                self.results[self._current_slug] = (installs, installs_display)
            self._current_slug = None
            self._current_name_parts = []
            self._current_install_parts = []
            self._capture_h3 = False
            self._capture_install_span = False

    def handle_data(self, data: str) -> None:
        if self._in_script or self._current_slug is None:
            return
        chunk = " ".join(data.split())
        if not chunk:
            return
        if self._capture_h3:
            self._current_name_parts.append(chunk)
        if self._capture_install_span:
            self._current_install_parts.append(chunk)

    def _match_skill_href(self, href: str) -> str | None:
        parts = [part for part in href.split("/") if part]
        if len(parts) != 3:
            return None
        owner, repo, slug = parts
        if owner != self._owner or repo != self._repo:
            return None
        return slug.strip()


def _parse_skills_sh_installs(
    *,
    html: str,
    owner: str,
    repo: str,
) -> dict[str, tuple[int, str]]:
    parser = _SkillsShRepoParser(owner=owner, repo=repo)
    parser.feed(html)
    return parser.results


def parse_compact_count(value: str) -> int | None:
    """Parse compact human-readable counts like `1.1K` into integers."""

    match = _COMPACT_COUNT_RE.fullmatch(" ".join(value.split()))
    if match is None:
        return None
    number = Decimal(match.group(1))
    suffix = (match.group(2) or "").upper()
    factor = {
        "": 1,
        "K": 1_000,
        "M": 1_000_000,
        "B": 1_000_000_000,
    }[suffix]
    return int(number * factor)


def format_compact_count(value: int | None) -> str:
    """Render large counts in a short human-readable form."""

    if value is None:
        return ""
    decimal_value = Decimal(value)
    thresholds = (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    )
    for index, (limit, suffix) in enumerate(thresholds):
        if decimal_value < limit:
            continue
        rounded, rendered = _format_compact_tier(decimal_value / limit, suffix)
        if rounded >= Decimal("1000") and index > 0:
            promoted_limit, promoted_suffix = thresholds[index - 1]
            _, rendered = _format_compact_tier(decimal_value / promoted_limit, promoted_suffix)
        return rendered
    return str(value)


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _format_compact_tier(value: Decimal, suffix: str) -> tuple[Decimal, str]:
    """Format one compact-count tier and keep the rounded numeric part for promotion checks."""

    precision = Decimal("1") if value >= 10 else Decimal("0.1")
    rounded = value.quantize(precision, rounding=ROUND_HALF_UP)
    return rounded, f"{format(rounded.normalize(), 'f')}{suffix}"
