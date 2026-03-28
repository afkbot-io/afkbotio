"""Tests for profile-local skill CRUD and manifest behavior."""

from __future__ import annotations

from pathlib import Path
import shutil

from _pytest.monkeypatch import MonkeyPatch
import afkbot.services.skills.doctor as doctor_module
import afkbot.services.skills.loader_availability as availability_module
from afkbot.services.skills.doctor import SkillDoctorService
from afkbot.services.skills.profile_service import ProfileSkillService
from afkbot.settings import Settings


async def test_profile_skill_upsert_preserves_existing_manifest_overlay(tmp_path: Path) -> None:
    """Updating SKILL.md should not overwrite an existing AFKBOT manifest contract."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSkillService(settings)

    skill_dir = tmp_path / "profiles/default/skills/doc"
    skill_dir.mkdir(parents=True)
    manifest_path = skill_dir / "AFKBOT.skill.toml"
    manifest_path.write_text(
        "\n".join(
            [
                "manifest_version = 1",
                'name = "doc"',
                'description = "Executable doc workflow."',
                'execution_mode = "executable"',
                'tool_names = ["bash.exec"]',
                "",
                "[requires]",
                'bins = ["python3"]',
                "env = []",
                "python_packages = []",
                "",
                "[source]",
                'kind = "local"',
                'id = "doc"',
                'url = ""',
            ]
        ),
        encoding="utf-8",
    )

    record = await service.upsert(
        profile_id="default",
        name="doc",
        content="# doc\n\nUse the doc workflow.",
    )

    assert record.execution_mode == "executable"
    assert record.tool_names == ("bash.exec",)
    assert manifest_path.read_text(encoding="utf-8").count('tool_names = ["bash.exec"]') == 1


async def test_profile_skill_upsert_repairs_invalid_manifest_overlay(tmp_path: Path) -> None:
    """Updating SKILL.md should repair an invalid adjacent AFKBOT manifest."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSkillService(settings)

    skill_dir = tmp_path / "profiles/default/skills/pdf"
    skill_dir.mkdir(parents=True)
    (skill_dir / "AFKBOT.skill.toml").write_text('manifest_version = "broken"\n', encoding="utf-8")

    record = await service.upsert(
        profile_id="default",
        name="pdf",
        content="# pdf\n\nUse reportlab to create PDF files.",
    )

    manifest_text = (skill_dir / "AFKBOT.skill.toml").read_text(encoding="utf-8")
    assert record.manifest_valid is True
    assert "manifest_version = 1" in manifest_text


async def test_skill_doctor_reports_missing_manifest_and_missing_surface(tmp_path: Path) -> None:
    """Doctor should make host-native skill contract gaps explicit."""

    settings = Settings(root_dir=tmp_path)
    skill_dir = tmp_path / "profiles/default/skills/advisory-doc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# advisory-doc\nUse me.", encoding="utf-8")

    executable_dir = tmp_path / "profiles/default/skills/dispatch-demo"
    executable_dir.mkdir(parents=True)
    (executable_dir / "SKILL.md").write_text("# dispatch-demo\nDispatch me.", encoding="utf-8")
    (executable_dir / "AFKBOT.skill.toml").write_text(
        "\n".join(
            [
                "manifest_version = 1",
                'name = "dispatch-demo"',
                'description = "Dispatch demo."',
                'execution_mode = "dispatch"',
                "tool_names = []",
                "app_names = []",
                "",
                "[requires]",
                "bins = []",
                "env = []",
                "python_packages = []",
                "",
                "[suggested]",
                "bins = []",
                "",
                "[source]",
                'kind = "local"',
                'id = "dispatch-demo"',
                'url = ""',
            ]
        ),
        encoding="utf-8",
    )

    doctor = SkillDoctorService(settings)
    items = await doctor.inspect_profile(profile_id="default")
    item_map = {item.name: item for item in items}

    assert "missing_manifest" in item_map["advisory-doc"].issues
    assert "missing_surface" in item_map["dispatch-demo"].issues
    assert "dispatch_missing_app" in item_map["dispatch-demo"].issues
    assert item_map["advisory-doc"].repair_commands == (
        "afk skill normalize --profile default advisory-doc",
    )


async def test_skill_doctor_returns_install_hints_for_missing_deps(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Doctor should emit actionable install hints for hard and suggested deps."""

    settings = Settings(root_dir=tmp_path)
    skill_dir = tmp_path / "profiles/default/skills/doc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Create and review .docx documents."',
                "---",
                "# DOCX",
                "",
                "```",
                "uv pip install python-docx pdf2image",
                "soffice --headless --convert-to pdf --outdir /tmp/out /tmp/demo.docx",
                "pdftoppm -png /tmp/out/demo.pdf /tmp/out/demo",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    doctor = SkillDoctorService(settings)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name == "soffice" else f"/usr/bin/{name}",
    )
    items = await doctor.inspect_profile(profile_id="default")
    record = {item.name: item for item in items}["doc"]

    assert any(
        hint.startswith("uv pip install ") and "pdf2image" in hint for hint in record.install_hints
    )
    assert any(
        hint.startswith("brew install ") or hint.startswith("sudo apt-get install -y ")
        for hint in record.install_hints
    )
    assert "bin:soffice" in record.missing_suggested_requirements
    assert "afk skill normalize --profile default doc" in record.repair_commands


async def test_skill_doctor_reports_suggested_bin_hints_without_blocking_skill(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Suggested bins should remain actionable hints without making skill unavailable."""

    settings = Settings(root_dir=tmp_path)
    skill_dir = tmp_path / "profiles/default/skills/doc"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                'description: "Create and review .docx documents."',
                "---",
                "# DOCX",
                "",
                "```",
                "uv pip install python-docx",
                "soffice --headless --convert-to pdf --outdir /tmp/out /tmp/demo.docx",
                "```",
            ]
        ),
        encoding="utf-8",
    )

    doctor = SkillDoctorService(settings)
    monkeypatch.setattr(
        availability_module,
        "has_python_package",
        lambda package_name: package_name == "python-docx",
    )
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "soffice" else f"/usr/bin/{name}"
    )
    items = await doctor.inspect_profile(profile_id="default")
    record = {item.name: item for item in items}["doc"]

    assert record.available is True
    assert record.missing_requirements == ()
    assert "bin:soffice" in record.missing_suggested_requirements
    assert any("libreoffice" in hint for hint in record.install_hints)


async def test_skill_doctor_returns_custom_install_hints_for_agent_clis(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Doctor should emit official install hints for bundled external agent CLIs."""

    settings = Settings(root_dir=tmp_path)
    skill_dir = tmp_path / "profiles/default/skills/agent-clis"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: agent-clis",
                'description: "Drive external agent CLIs from the shell."',
                "tool_names:",
                "  - bash.exec",
                "requires_bins:",
                "  - codex",
                "  - gemini",
                "  - claude",
                "  - aider",
                "---",
                "# agent-clis",
            ]
        ),
        encoding="utf-8",
    )

    doctor = SkillDoctorService(settings)
    monkeypatch.setattr(doctor_module.sys, "platform", "linux")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name in {"codex", "gemini", "claude", "aider"} else f"/usr/bin/{name}",
    )
    items = await doctor.inspect_profile(profile_id="default")
    record = {item.name: item for item in items}["agent-clis"]

    assert record.available is False
    assert "bin:codex" in record.missing_requirements
    assert "bin:gemini" in record.missing_requirements
    assert "bin:claude" in record.missing_requirements
    assert "bin:aider" in record.missing_requirements
    assert any("@openai/codex" in hint for hint in record.install_hints)
    assert any("@google/gemini-cli" in hint for hint in record.install_hints)
    assert any("claude.ai/install.sh" in hint for hint in record.install_hints)
    assert any("aider.chat/install.sh" in hint for hint in record.install_hints)


async def test_skill_doctor_returns_windows_install_hints_for_claude_cli(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Windows doctor output should include the native Claude Code install path."""

    settings = Settings(root_dir=tmp_path)
    skill_dir = tmp_path / "profiles/default/skills/claude-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: claude-skill",
                'description: "Drive Claude Code from the shell."',
                "tool_names:",
                "  - bash.exec",
                "requires_bins:",
                "  - claude",
                "---",
                "# claude-skill",
            ]
        ),
        encoding="utf-8",
    )

    doctor = SkillDoctorService(settings)
    monkeypatch.setattr(doctor_module.sys, "platform", "win32")
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "claude" else f"/usr/bin/{name}"
    )

    items = await doctor.inspect_profile(profile_id="default")
    record = {item.name: item for item in items}["claude-skill"]

    assert "bin:claude" in record.missing_requirements
    assert "winget install Anthropic.ClaudeCode" in record.install_hints
    assert not any("brew install --cask claude-code" in hint for hint in record.install_hints)
    assert not any("claude.ai/install.sh" in hint for hint in record.install_hints)


async def test_profile_skill_normalize_manifests_creates_and_repairs(tmp_path: Path) -> None:
    """Normalize should create missing manifests and repair invalid ones without touching valid ones."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileSkillService(settings)

    created_dir = tmp_path / "profiles/default/skills/doc"
    created_dir.mkdir(parents=True)
    (created_dir / "SKILL.md").write_text(
        "# doc\n\nUse python-docx to create docx files.", encoding="utf-8"
    )

    repaired_dir = tmp_path / "profiles/default/skills/pdf"
    repaired_dir.mkdir(parents=True)
    (repaired_dir / "SKILL.md").write_text(
        "# pdf\n\nUse reportlab to create PDF files.", encoding="utf-8"
    )
    (repaired_dir / "AFKBOT.skill.toml").write_text(
        'manifest_version = "broken"\n', encoding="utf-8"
    )

    valid_dir = tmp_path / "profiles/default/skills/imap"
    valid_dir.mkdir(parents=True)
    (valid_dir / "SKILL.md").write_text("# imap\n\nUse IMAP workflows.", encoding="utf-8")
    valid_manifest_path = valid_dir / "AFKBOT.skill.toml"
    valid_manifest_path.write_text(
        "\n".join(
            [
                "manifest_version = 1",
                'name = "imap"',
                'description = "IMAP workflow."',
                'execution_mode = "executable"',
                'tool_names = ["credentials.list", "credentials.request", "app.run"]',
                'app_names = ["imap"]',
                'preferred_tool_order = ["credentials.list", "credentials.request", "app.run"]',
                "",
                "[requires]",
                "bins = []",
                "env = []",
                "python_packages = []",
                "",
                "[source]",
                'kind = "local"',
                'id = "imap"',
                'url = ""',
            ]
        ),
        encoding="utf-8",
    )

    records = await service.normalize_manifests(profile_id="default")
    record_map = {item.name: item for item in records}

    assert record_map["doc"].action == "created"
    assert record_map["pdf"].action == "repaired"
    assert record_map["imap"].action == "skipped"
    assert (created_dir / "AFKBOT.skill.toml").exists()
    assert "manifest_version = 1" in (created_dir / "AFKBOT.skill.toml").read_text(encoding="utf-8")
    assert "manifest_version = 1" in (repaired_dir / "AFKBOT.skill.toml").read_text(
        encoding="utf-8"
    )

    overwritten = await service.normalize_manifests(
        profile_id="default", name="imap", overwrite=True
    )
    assert len(overwritten) == 1
    assert overwritten[0].action == "overwritten"
    assert valid_manifest_path.exists()
