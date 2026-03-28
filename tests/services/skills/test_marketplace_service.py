"""Tests for marketplace source parsing, ranking, and installation behavior."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceListItem,
    SkillMarketplaceListResult,
)
from afkbot.services.skills.marketplace_popularity import (
    MarketplacePopularityResolver,
    format_compact_count,
    parse_compact_count,
)
from afkbot.services.skills.marketplace_service import SkillMarketplaceError, SkillMarketplaceService
from afkbot.settings import Settings


def test_marketplace_rejects_direct_skill_markdown_on_untrusted_host(tmp_path: Path) -> None:
    """Marketplace should reject direct SKILL.md URLs outside configured hosts."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))

    # Act / Assert
    with pytest.raises(SkillMarketplaceError, match="Unsupported source host"):
        service._parse_source("https://evil.example/skills/demo/SKILL.md")  # noqa: SLF001


def test_marketplace_allows_raw_github_skill_markdown(tmp_path: Path) -> None:
    """Configured raw GitHub hosts should still support direct SKILL.md sources."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))

    # Act
    descriptor = service._parse_source(  # noqa: SLF001
        "https://raw.githubusercontent.com/acme/demo/main/skills/github/SKILL.md"
    )

    # Assert
    assert descriptor.direct_url is not None
    assert descriptor.skill_hint == "github"
    assert descriptor.owner == "acme"
    assert descriptor.repo == "demo"
    assert descriptor.ref == "main"


async def test_marketplace_search_matches_name_and_summary(tmp_path: Path) -> None:
    """Marketplace search should filter by normalized name and summary text."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))

    async def _fake_list_source(*, source: str, limit=None, profile_id: str | None = None):
        assert source == "default"
        assert limit is None
        assert profile_id is None
        return SkillMarketplaceListResult(
            source="skills.sh/openai/skills",
            items=(
                SkillMarketplaceListItem(
                    name="doc",
                    summary="Create and edit docx files",
                    path="skills/doc/SKILL.md",
                    source="skills.sh/openai/skills",
                ),
                SkillMarketplaceListItem(
                    name="pdf",
                    summary="Create PDF reports",
                    path="skills/pdf/SKILL.md",
                    source="skills.sh/openai/skills",
                ),
            ),
        )

    service.list_source = _fake_list_source  # type: ignore[method-assign]

    # Act
    result = await service.search_source(source="default", query="docx edit")

    # Assert
    assert [item.name for item in result.items] == ["doc"]


async def test_marketplace_search_ignores_install_count_display(tmp_path: Path) -> None:
    """Marketplace search should not match numeric install badges as free-text content."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))

    async def _fake_list_source(*, source: str, limit=None, profile_id: str | None = None):
        assert source == "default"
        assert limit is None
        assert profile_id is None
        return SkillMarketplaceListResult(
            source="skills.sh/openai/skills",
            items=(
                SkillMarketplaceListItem(
                    name="memory",
                    summary="Store reusable notes",
                    path="skills/memory/SKILL.md",
                    source="skills.sh/openai/skills",
                    installs_display="33.3K",
                ),
            ),
        )

    service.list_source = _fake_list_source  # type: ignore[method-assign]

    # Act
    result = await service.search_source(source="default", query="33")

    # Assert
    assert result.items == ()


async def test_marketplace_list_and_search_apply_limit(tmp_path: Path) -> None:
    """Marketplace list/search should bound result count with the provided limit."""

    # Arrange
    service = SkillMarketplaceService(
        Settings(root_dir=tmp_path),
        fetch_text=_build_marketplace_fetch_text(),
    )

    def _fake_list_repo_source_sync(_descriptor):
        return [
            SkillMarketplaceListItem(
                name=f"skill-{index}",
                summary=f"summary {index}",
                path=f"skills/skill-{index}/SKILL.md",
                source="skills.sh/openai/skills",
                canonical_source=f"https://raw.githubusercontent.com/acme/demo/main/skills/skill-{index}/SKILL.md",
            )
            for index in range(1, 101)
        ]

    service._list_repo_source_sync = _fake_list_repo_source_sync  # type: ignore[method-assign]

    # Act
    listed = await service.list_source(source="default", limit=50)
    searched = await service.search_source(source="default", query="skill", limit=50)

    # Assert
    assert len(listed.items) == 50
    assert len(searched.items) == 50


async def test_marketplace_list_enriches_repo_results_with_ranking_stats(tmp_path: Path) -> None:
    """Marketplace listing should merge `skills.sh` installs and GitHub repo stats."""

    # Arrange
    service = SkillMarketplaceService(
        Settings(root_dir=tmp_path),
        fetch_text=_build_marketplace_fetch_text(),
    )

    # Act
    result = await service.list_source(source="skills.sh/openai/skills", limit=10)

    # Assert
    assert result.source == "skills.sh/openai/skills"
    assert result.source_stats.installs_source == "skills.sh"
    assert result.source_stats.total_installs == 16_400
    assert result.source_stats.total_installs_display == "16.4K"
    assert result.source_stats.repo_social_source == "github"
    assert result.source_stats.repo_stars == 14_572
    assert result.source_stats.repo_forks == 841
    assert result.source_stats.repo_watchers == 85
    assert [item.name for item in result.items] == ["pdf", "memory"]
    assert result.items[0].rank == 1
    assert result.items[0].installs == 528
    assert result.items[0].installs_display == "528"
    assert result.items[1].rank == 2
    assert result.items[1].installs == 362
    assert result.items[1].canonical_source.endswith("/skills/memory/SKILL.md")


async def test_marketplace_list_marks_installed_items_by_source_and_name(tmp_path: Path) -> None:
    """Marketplace listing should mark installed items even after local renames."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    service = SkillMarketplaceService(
        settings,
        fetch_text=_build_marketplace_fetch_text(),
    )
    memory_path = service._loader.profile_skill_path("default", "memory-local")  # noqa: SLF001
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("# memory-local\nProfile-local memory wrapper.", encoding="utf-8")
    service._loader.materialize_manifest(  # noqa: SLF001
        skill_path=memory_path,
        name="memory-local",
        content=memory_path.read_text(encoding="utf-8"),
        source_kind="marketplace",
        source_id="https://raw.githubusercontent.com/openai/skills/main/skills/memory/SKILL.md",
        source_url="https://raw.githubusercontent.com/openai/skills/main/skills/memory/SKILL.md",
    )
    core_skill_dir = tmp_path / "afkbot/skills/pdf"
    core_skill_dir.mkdir(parents=True, exist_ok=True)
    (core_skill_dir / "SKILL.md").write_text("# pdf\nBuilt-in PDF helper.", encoding="utf-8")

    # Act
    result = await service.list_source(
        source="skills.sh/openai/skills",
        profile_id="default",
        limit=10,
    )

    # Assert
    items = {item.name: item for item in result.items}
    assert items["memory"].installed is True
    assert items["memory"].installed_name == "memory-local"
    assert items["memory"].installed_origin == "profile"
    assert items["pdf"].installed is True
    assert items["pdf"].installed_name == "pdf"
    assert items["pdf"].installed_origin == "core"


async def test_marketplace_list_does_not_mark_manual_profile_skill_by_name(tmp_path: Path) -> None:
    """Marketplace listing should not treat unrelated manual profile skills as installed."""

    # Arrange
    service = SkillMarketplaceService(
        Settings(root_dir=tmp_path),
        fetch_text=_build_marketplace_fetch_text(),
    )
    manual_path = service._loader.profile_skill_path("default", "memory")  # noqa: SLF001
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text("# memory\nProfile-local custom skill.", encoding="utf-8")
    service._loader.materialize_manifest(  # noqa: SLF001
        skill_path=manual_path,
        name="memory",
        content=manual_path.read_text(encoding="utf-8"),
    )

    # Act
    result = await service.list_source(
        source="skills.sh/openai/skills",
        profile_id="default",
        limit=10,
    )

    # Assert
    items = {item.name: item for item in result.items}
    assert items["memory"].installed is False
    assert items["memory"].installed_name is None
    assert items["memory"].installed_origin is None


def test_marketplace_ranking_keeps_zero_installs_ahead_of_unknown() -> None:
    """Marketplace ranking should keep explicit zero-install items ahead of unknown ones."""

    # Arrange
    items = [
        SkillMarketplaceListItem(name="unknown", source="skills.sh/openai/skills", path="skills/unknown/SKILL.md"),
        SkillMarketplaceListItem(name="zero", source="skills.sh/openai/skills", path="skills/zero/SKILL.md"),
        SkillMarketplaceListItem(name="popular", source="skills.sh/openai/skills", path="skills/popular/SKILL.md"),
    ]

    # Act
    result_items = MarketplacePopularityResolver._merge_install_stats(  # noqa: SLF001
        items=items,
        installs_by_name={
            "popular": (12, "12"),
            "zero": (0, "0"),
        },
    )

    # Assert
    assert [item.name for item in result_items] == ["popular", "zero", "unknown"]
    assert [item.rank for item in result_items] == [1, 2, 3]
    assert result_items[1].installs == 0


def test_parse_compact_count_avoids_float_truncation() -> None:
    """Compact counts should parse exact decimal values without float drift."""

    # Arrange
    value = "33.3K"

    # Act
    parsed = parse_compact_count(value)

    # Assert
    assert parsed == 33_300


def test_format_compact_count_normalizes_threshold_boundaries() -> None:
    """Compact count formatting should avoid awkward rounded boundary output."""

    # Arrange
    lower_threshold_value = 9_950
    upper_threshold_value = 999_500

    # Act
    lower_formatted = format_compact_count(lower_threshold_value)
    upper_formatted = format_compact_count(upper_threshold_value)

    # Assert
    assert lower_formatted == "10K"
    assert upper_formatted == "1M"


async def test_marketplace_list_preserves_source_stats_without_item_rankings(tmp_path: Path) -> None:
    """Marketplace listing should keep source stats even when per-item install rows disappear."""

    # Arrange
    service = SkillMarketplaceService(
        Settings(root_dir=tmp_path),
        fetch_text=_build_marketplace_fetch_text(
            skills_html="\n".join(
                [
                    "<html><body>",
                    "<span>16.4K total installs</span>",
                    "<div>No parseable install rows.</div>",
                    "</body></html>",
                ]
            ),
        ),
    )

    # Act
    result = await service.list_source(source="skills.sh/openai/skills", limit=10)

    # Assert
    assert result.source_stats.installs_source == "skills.sh"
    assert result.source_stats.total_installs == 16_400
    assert result.source_stats.total_installs_display == "16.4K"
    assert result.items[0].rank is None
    assert result.items[0].installs is None


async def test_marketplace_install_preserves_existing_manifest_overlay(tmp_path: Path) -> None:
    """Reinstalling one marketplace skill should not overwrite local manifest tuning."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))
    skill_dir = tmp_path / "profiles/default/skills/doc"
    skill_dir.mkdir(parents=True)
    manifest_path = skill_dir / "AFKBOT.skill.toml"
    manifest_path.write_text(
        "\n".join(
            [
                'manifest_version = 1',
                'name = "doc"',
                'description = "Executable doc workflow."',
                'execution_mode = "executable"',
                'tool_names = ["bash.exec"]',
                "",
                "[requires]",
                'bins = ["python3"]',
                'env = []',
                'python_packages = []',
                "",
                "[source]",
                'kind = "marketplace"',
                'id = "skills.sh/doc"',
                'url = "https://skills.sh/doc"',
            ]
        ),
        encoding="utf-8",
    )

    def _fake_resolve_markdown_sync(descriptor, requested_skill):
        _ = descriptor, requested_skill
        return "# doc\nUse the doc workflow.", "skills.sh/doc", "doc"

    service._resolve_markdown_sync = _fake_resolve_markdown_sync  # type: ignore[method-assign]

    # Act
    record = await service.install(
        profile_id="default",
        source="default",
        skill="doc",
        overwrite=True,
    )

    # Assert
    assert record.execution_mode == "executable"
    assert record.manifest_path == "profiles/default/skills/doc/AFKBOT.skill.toml"
    assert manifest_path.read_text(encoding="utf-8").count('tool_names = ["bash.exec"]') == 1


async def test_marketplace_install_repairs_invalid_manifest_overlay(tmp_path: Path) -> None:
    """Marketplace install should repair an invalid adjacent AFKBOT manifest."""

    # Arrange
    service = SkillMarketplaceService(Settings(root_dir=tmp_path))
    skill_dir = tmp_path / "profiles/default/skills/pdf"
    skill_dir.mkdir(parents=True)
    manifest_path = skill_dir / "AFKBOT.skill.toml"
    manifest_path.write_text('manifest_version = "broken"\n', encoding="utf-8")

    def _fake_resolve_markdown_sync(descriptor, requested_skill):
        _ = descriptor, requested_skill
        return "# pdf\nUse reportlab to create PDF files.", "skills.sh/pdf", "pdf"

    service._resolve_markdown_sync = _fake_resolve_markdown_sync  # type: ignore[method-assign]

    # Act
    record = await service.install(
        profile_id="default",
        source="default",
        skill="pdf",
        overwrite=True,
    )

    # Assert
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert record.manifest_path == "profiles/default/skills/pdf/AFKBOT.skill.toml"
    assert 'manifest_version = 1' in manifest_text
    assert record.execution_mode in {"advisory", "executable"}


def _build_marketplace_fetch_text(*, skills_html: str | None = None) -> Callable[[str, int], str]:
    responses = {
        "https://api.github.com/repos/openai/skills/git/trees/main?recursive=1": json.dumps(
            {
                "tree": [
                    {"type": "blob", "path": "skills/memory/SKILL.md"},
                    {"type": "blob", "path": "skills/pdf/SKILL.md"},
                ]
            }
        ),
        "https://api.github.com/repos/openai/skills": json.dumps(
            {
                "stargazers_count": 14572,
                "forks_count": 841,
                "subscribers_count": 85,
            }
        ),
        "https://skills.sh/openai/skills": skills_html
        or "\n".join(
            [
                "<html><body>",
                "<span>16.4K total installs</span>",
                '<a href="/openai/skills/pdf">',
                "<h3>pdf</h3>",
                '<span class="font-mono text-sm text-foreground">528</span>',
                "</a>",
                '<a href="/openai/skills/memory">',
                "<h3>memory</h3>",
                '<span class="font-mono text-sm text-foreground">362</span>',
                "</a>",
                "</body></html>",
            ]
        ),
    }

    def _fetch_text(url: str, _max_bytes: int) -> str:
        try:
            return responses[url]
        except KeyError as exc:  # pragma: no cover - defensive test guard
            raise AssertionError(f"Unexpected URL: {url}") from exc

    return _fetch_text
