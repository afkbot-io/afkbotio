"""Shared transport serializers for marketplace contracts."""

from __future__ import annotations

from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceInstallRecord,
    SkillMarketplaceListItem,
    SkillMarketplaceSourceStats,
)


def marketplace_install_record_to_payload(record: SkillMarketplaceInstallRecord) -> dict[str, object]:
    """Serialize one marketplace install record for transport payloads."""

    return {
        "name": record.name,
        "path": record.path,
        "source": record.source,
        "summary": record.summary,
        "manifest_path": record.manifest_path,
        "available": record.available,
        "missing_requirements": list(record.missing_requirements),
        "execution_mode": record.execution_mode,
    }


def marketplace_list_item_to_payload(item: SkillMarketplaceListItem) -> dict[str, object]:
    """Serialize one marketplace listing item for transport payloads."""

    return {
        "name": item.name,
        "source": item.source,
        "path": item.path,
        "summary": item.summary,
        "canonical_source": item.canonical_source,
        "rank": item.rank,
        "installs": item.installs,
        "installs_display": item.installs_display,
        "installed": item.installed,
        "installed_name": item.installed_name,
        "installed_origin": item.installed_origin,
    }


def marketplace_source_stats_to_payload(source_stats: SkillMarketplaceSourceStats) -> dict[str, object]:
    """Serialize source-level marketplace stats for transport payloads."""

    return {
        "installs_source": source_stats.installs_source,
        "total_installs": source_stats.total_installs,
        "total_installs_display": source_stats.total_installs_display,
        "repo_social_source": source_stats.repo_social_source,
        "repo_stars": source_stats.repo_stars,
        "repo_forks": source_stats.repo_forks,
        "repo_watchers": source_stats.repo_watchers,
    }
