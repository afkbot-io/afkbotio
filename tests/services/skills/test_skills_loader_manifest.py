"""Manifest parsing and availability tests for the markdown skills loader."""

from __future__ import annotations

from pathlib import Path

from tests.services.skills._loader_harness import build_loader, write_manifest, write_skill


async def test_skills_loader_parses_manifest_lists_and_flags(tmp_path: Path) -> None:
    """Loader should expose normalized manifest fields for routing."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "afkbot/skills/telegram",
        "\n".join(
            [
                "---",
                'description: "Telegram skill."',
                "aliases:",
                "  - tg",
                "  - telegram-bot",
                "triggers:",
                "  - telegram",
                "  - телеграм",
                "tool_names:",
                "  - credentials.list",
                "  - app.run",
                "app_names:",
                "  - telegram",
                "preferred_tool_order:",
                "  - credentials.list",
                "  - app.run",
                "always_on: true",
                'execution_mode: "executable"',
                "---",
                "# telegram",
            ],
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    manifest = {item.name: item for item in skills}["telegram"].manifest
    assert manifest.aliases == ("tg", "telegram-bot")
    assert manifest.triggers == ("telegram", "телеграм")
    assert manifest.tool_names == ("credentials.list", "app.run")
    assert manifest.app_names == ("telegram",)
    assert manifest.preferred_tool_order == ("credentials.list", "app.run")
    assert manifest.always_on is True
    assert manifest.execution_mode == "executable"


async def test_skills_loader_without_manifest_surface_stays_advisory(tmp_path: Path) -> None:
    """Missing AFKBOT manifest tool surface should remain fail-closed."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "afkbot/skills/doc",
        "\n".join(
            [
                "---",
                'description: "Create and review .docx documents."',
                "---",
                "# DOCX",
                "",
                "Use when tasks involve reading, creating, or editing `.docx` documents.",
                "Write final artifacts under `output/doc/` and inspect them visually.",
                "",
                "```",
                "uv pip install python-docx pdf2image",
                "brew install libreoffice poppler",
                "python3 scripts/render_docx.py /tmp/demo.docx --output_dir /tmp/pages",
                "```",
                "",
                "```",
                "soffice --headless --convert-to pdf --outdir /tmp/out /tmp/demo.docx",
                "pdftoppm -png /tmp/out/demo.pdf /tmp/out/demo",
                "```",
            ],
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    doc_info = {item.name: item for item in skills}["doc"]
    assert doc_info.manifest.execution_mode == "advisory"
    assert doc_info.manifest.tool_names == ()
    assert doc_info.manifest.preferred_tool_order == ()
    assert "python-docx" in doc_info.manifest.requires_python_packages
    assert "pdf2image" in doc_info.manifest.requires_python_packages
    assert doc_info.manifest.requires_bins == ()
    assert "soffice" in doc_info.manifest.suggested_bins
    assert "pdftoppm" in doc_info.manifest.suggested_bins


async def test_skills_loader_reads_browser_control_manifest_overlay(tmp_path: Path) -> None:
    """Browser control skill should expose explicit browser routing metadata from AFKBOT manifest."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    skill_dir = write_skill(
        tmp_path,
        "afkbot/skills/browser-control",
        "\n".join(
            [
                "---",
                "name: browser-control",
                'description: "Browser skill."',
                "---",
                "# browser-control",
                "",
                "- `browser.control`",
            ]
        ),
    )
    write_manifest(
        skill_dir,
        "\n".join(
            [
                'manifest_version = 1',
                'name = "browser-control"',
                'description = "Browser skill."',
                'execution_mode = "executable"',
                "always_on = false",
                'aliases = ["browser"]',
                'triggers = ["через браузер", "browser"]',
                'tool_names = ["browser.control"]',
                'app_names = []',
                'preferred_tool_order = ["browser.control"]',
                "",
                "[requires]",
                "bins = []",
                "env = []",
                'python_packages = ["playwright"]',
                "",
                "[suggested]",
                "bins = []",
                "",
                "[source]",
                'kind = ""',
                'id = ""',
                'url = ""',
            ]
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    manifest = {item.name: item for item in skills}["browser-control"].manifest
    assert manifest.execution_mode == "executable"
    assert manifest.aliases == ("browser",)
    assert manifest.triggers == ("через браузер", "browser")
    assert manifest.tool_names == ("browser.control",)
    assert manifest.preferred_tool_order == ("browser.control",)
    assert manifest.requires_python_packages == ("playwright",)


async def test_skills_loader_does_not_block_availability_on_inferred_bins(tmp_path: Path) -> None:
    """Inferred shell binaries should be install hints, not hard blockers."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "profiles/default/skills/demo",
        "\n".join(
            [
                "---",
                'description: "Demo skill."',
                "---",
                "# demo",
                "",
                "```bash",
                "definitely-missing-binary --render file.docx",
                "```",
            ],
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    demo_info = {item.name: item for item in skills}["demo"]
    assert demo_info.available is True
    assert demo_info.missing_requirements == ()
    assert demo_info.missing_suggested_requirements == ("bin:definitely-missing-binary",)
    assert demo_info.manifest.suggested_bins == ("definitely-missing-binary",)


async def test_skills_loader_pdf_keywords_do_not_grant_surface_without_manifest(tmp_path: Path) -> None:
    """PDF-only wording should not infer executable tool surface."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "profiles/default/skills/pdf",
        "\n".join(
            [
                "---",
                'description: "Create and review PDF files."',
                "---",
                "# PDF",
                "",
                "Use reportlab to create PDFs and pdftoppm for rendering.",
                "",
                "```",
                "uv pip install reportlab pdfplumber pypdf",
                "pdftoppm -png $INPUT_PDF $OUTPUT_PREFIX",
                "```",
            ],
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    pdf_info = {item.name: item for item in skills}["pdf"]
    assert pdf_info.manifest.execution_mode == "advisory"
    assert pdf_info.manifest.tool_names == ()
    assert pdf_info.manifest.preferred_tool_order == ()
    assert pdf_info.manifest.requires_python_packages == ("reportlab", "pdfplumber", "pypdf")
    assert pdf_info.manifest.suggested_bins == ("pdftoppm",)


async def test_skills_loader_does_not_infer_bins_from_markdown_templates(tmp_path: Path) -> None:
    """Non-shell markdown example blocks must not create fake bin requirements."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "afkbot/skills/skill-creator",
        "\n".join(
            [
                "---",
                'description: "Manage profile skills."',
                "tool_names:",
                "  - skill.profile.list",
                "---",
                "# skill-creator",
                "",
                "```md",
                "---",
                "name: example-skill",
                "aliases:",
                "  - alias-one",
                "---",
                "# Example Skill",
                "Use this skill when the task matches the description above.",
                "```",
            ],
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    creator_info = {item.name: item for item in skills}["skill-creator"]
    assert creator_info.available is True
    assert creator_info.manifest.requires_bins == ()
    assert creator_info.missing_requirements == ()


async def test_skills_loader_marks_unavailable_when_requirements_missing(tmp_path: Path) -> None:
    """Loader should expose unmet env/bin requirements in metadata."""

    # Arrange
    write_skill(
        tmp_path,
        "afkbot/skills/needs-env",
        "---\nrequires_env: MUST_EXIST_ENV\nrequires_bins: definitely-missing-binary\n---\n# demo",
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "needs-env" in skill_map
    assert skill_map["needs-env"].available is False
    assert "env:MUST_EXIST_ENV" in skill_map["needs-env"].missing_requirements
    assert "bin:definitely-missing-binary" in skill_map["needs-env"].missing_requirements


async def test_skills_loader_treats_persisted_afkbot_runtime_secrets_as_available(
    tmp_path: Path,
) -> None:
    """AFKBOT_* env requirements should accept resolved settings-backed runtime secrets."""

    # Arrange
    write_skill(tmp_path, "afkbot/skills/security-secrets", "# security")
    write_skill(
        tmp_path,
        "afkbot/skills/imap",
        "---\nrequires_env: AFKBOT_CREDENTIALS_MASTER_KEYS\n---\n# imap",
    )
    loader = build_loader(tmp_path, credentials_master_keys="persisted-master-key")

    # Act
    skills = await loader.list_skills("default")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert skill_map["imap"].available is True
    assert skill_map["imap"].missing_requirements == ()


async def test_skills_loader_marks_unavailable_for_os_mismatch(tmp_path: Path) -> None:
    """OS-gated skills should be marked unavailable on non-matching platform."""

    # Arrange
    write_skill(
        tmp_path,
        "afkbot/skills/os-only",
        "---\nos: definitely-not-this-platform\n---\n# demo",
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    skill_map = {item.name: item for item in skills}
    assert "os-only" in skill_map
    assert skill_map["os-only"].available is False
    assert "os" in skill_map["os-only"].missing_requirements


async def test_skills_loader_merges_afkbot_manifest_overlay(tmp_path: Path) -> None:
    """Executable host-native manifest overlay should override frontmatter contract fields."""

    # Arrange
    skill_dir = write_skill(
        tmp_path,
        "afkbot/skills/doc",
        "\n".join(
            [
                "---",
                'description: "Doc operations."',
                "tool_names:",
                "  - file.read",
                "---",
                "# doc",
            ]
        ),
    )
    write_manifest(
        skill_dir,
        "\n".join(
            [
                'manifest_version = 1',
                'name = "doc"',
                'description = "Executable doc workflow."',
                'execution_mode = "executable"',
                'tool_names = ["bash.exec"]',
                'preferred_tool_order = ["bash.exec"]',
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
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    manifest = {item.name: item for item in skills}["doc"].manifest
    assert manifest.description == "Executable doc workflow."
    assert manifest.execution_mode == "executable"
    assert manifest.tool_names == ("bash.exec",)
    assert manifest.preferred_tool_order == ("bash.exec",)
    assert manifest.source_kind == "marketplace"
    assert manifest.source_id == "skills.sh/doc"


async def test_skills_loader_treats_legacy_advisory_only_as_execution_mode_hint(
    tmp_path: Path,
) -> None:
    """Legacy advisory_only metadata should only act as a compatibility hint."""

    # Arrange
    write_skill(
        tmp_path,
        "afkbot/skills/doc",
        "\n".join(
            [
                "---",
                'description: "Doc workflow."',
                "tool_names:",
                "  - file.read",
                "advisory_only: true",
                "---",
                "# doc",
            ]
        ),
    )
    loader = build_loader(tmp_path)

    # Act
    skills = await loader.list_skills("default")

    # Assert
    manifest = {item.name: item for item in skills}["doc"].manifest
    assert manifest.execution_mode == "advisory"
    assert manifest.tool_names == ("file.read",)
