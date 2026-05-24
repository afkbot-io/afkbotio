"""Tests for packaged AI team subagent templates."""

from __future__ import annotations

from pathlib import Path


def test_packaged_team_subagent_templates_exist() -> None:
    """Core install should ship a complete starter AI team."""

    root = Path(__file__).resolve().parents[3] / "afkbot" / "subagents"
    expected = {
        "architect.md",
        "backend-engineer.md",
        "devops.md",
        "docs-writer.md",
        "frontend-engineer.md",
        "qa-engineer.md",
        "researcher.md",
        "reviewer.md",
    }

    assert expected.issubset({path.name for path in root.glob("*.md")})


def test_packaged_team_templates_include_taskflow_contract() -> None:
    """Starter roles must teach agents to use durable Task Flow collaboration."""

    root = Path(__file__).resolve().parents[3] / "afkbot" / "subagents"
    team_templates = (
        "architect.md",
        "backend-engineer.md",
        "devops.md",
        "docs-writer.md",
        "frontend-engineer.md",
        "qa-engineer.md",
        "researcher.md",
        "reviewer.md",
    )
    for name in team_templates:
        path = root / name
        body = path.read_text(encoding="utf-8")
        assert "Task Flow" in body, path.name
        assert "task.context.get" in body or "Task Flow Context Bundle" in body, path.name
        assert "comment" in body.lower(), path.name

    assert not (root / "orchestrator.md").exists()
