"""Tests for top-level memory/skill/subagent CLI groups."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    get_channel_binding_service,
    reset_channel_binding_services_async,
)
from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceInstallRecord,
    SkillMarketplaceListItem,
    SkillMarketplaceListResult,
    SkillMarketplaceSourceStats,
)
from afkbot.services.skills.doctor import SkillDoctorRecord
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'assets.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def _create_profile(runner: CliRunner) -> None:
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "default",
            "--name",
            "Default",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert result.exit_code == 0


def test_memory_cli_crud_and_profiles(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Top-level memory CLI should expose CRUD/search/profile listing."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    set_result = runner.invoke(
        app,
        [
            "memory",
            "set",
            "project-note",
            "--profile",
            "default",
            "--text",
            "Need to follow up with infra.",
            "--source",
            "manual",
        ],
    )
    assert set_result.exit_code == 0
    payload = json.loads(set_result.stdout)
    assert payload["memory"]["memory_key"] == "project-note"
    assert payload["memory"]["source"] == "manual"

    show_result = runner.invoke(
        app,
        ["memory", "show", "project-note", "--profile", "default"],
    )
    assert show_result.exit_code == 0
    assert json.loads(show_result.stdout)["memory"]["content"] == "Need to follow up with infra."

    search_result = runner.invoke(
        app,
        ["memory", "search", "infra", "--profile", "default", "--json"],
    )
    assert search_result.exit_code == 0
    assert json.loads(search_result.stdout)["count"] == 1

    list_result = runner.invoke(
        app,
        ["memory", "list", "--profile", "default", "--json"],
    )
    assert list_result.exit_code == 0
    assert json.loads(list_result.stdout)["count"] == 1

    digest_result = runner.invoke(
        app,
        ["memory", "digest", "--profile", "default", "--json"],
    )
    assert digest_result.exit_code == 0
    digest_payload = json.loads(digest_result.stdout)
    assert digest_payload["item_count"] == 1
    assert "project-note" in digest_payload["digest_md"]

    profiles_result = runner.invoke(app, ["memory", "profiles", "--json"])
    assert profiles_result.exit_code == 0
    assert json.loads(profiles_result.stdout)["profiles"] == ["default"]

    delete_result = runner.invoke(
        app,
        ["memory", "delete", "project-note", "--profile", "default"],
    )
    assert delete_result.exit_code == 0
    assert json.loads(delete_result.stdout)["ok"] is True


def test_memory_cli_search_profile_scope_includes_local_items(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Profile-scope memory search should include regular local profile records."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    set_result = runner.invoke(
        app,
        [
            "memory",
            "set",
            "profile-local",
            "--profile",
            "default",
            "--text",
            "Profile local note about infra",
        ],
    )
    assert set_result.exit_code == 0

    search_result = runner.invoke(
        app,
        ["memory", "search", "infra", "--profile", "default", "--scope", "profile", "--json"],
    )

    assert search_result.exit_code == 0
    payload = json.loads(search_result.stdout)
    assert payload["count"] == 1
    assert payload["items"][0]["memory_key"] == "profile-local"


def test_memory_cli_scoped_binding_and_promote(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Top-level memory CLI should support chat-scoped records resolved from binding ids."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)
    settings = get_settings()
    asyncio.run(
        get_channel_binding_service(settings).put(
            ChannelBindingRule(
                binding_id="telegram-sales",
                transport="telegram_user",
                profile_id="default",
                session_policy="per-chat",
                account_id="personal-user",
                peer_id="200",
            )
        )
    )
    asyncio.run(reset_channel_binding_services_async())

    set_result = runner.invoke(
        app,
        [
            "memory",
            "set",
            "sales-preference",
            "--profile",
            "default",
            "--binding-id",
            "telegram-sales",
            "--scope",
            "auto",
            "--summary",
            "This chat prefers Telegram-first workflows.",
            "--memory-kind",
            "preference",
        ],
    )
    assert set_result.exit_code == 0
    payload = json.loads(set_result.stdout)
    assert payload["scope"]["scope_kind"] == "chat"
    assert payload["memory"]["scope_kind"] == "chat"

    search_result = runner.invoke(
        app,
        [
            "memory",
            "search",
            "Telegram-first workflows",
            "--profile",
            "default",
            "--binding-id",
            "telegram-sales",
            "--scope",
            "auto",
            "--json",
        ],
    )
    assert search_result.exit_code == 0
    search_payload = json.loads(search_result.stdout)
    assert search_payload["scope"]["binding_id"] == "telegram-sales"
    assert search_payload["items"][0]["memory_key"] == "sales-preference"

    promote_result = runner.invoke(
        app,
        [
            "memory",
            "promote",
            "sales-preference",
            "--profile",
            "default",
            "--binding-id",
            "telegram-sales",
            "--scope",
            "auto",
        ],
    )
    assert promote_result.exit_code == 0
    assert json.loads(promote_result.stdout)["memory"]["visibility"] == "promoted_global"

    digest_result = runner.invoke(
        app,
        [
            "memory",
            "digest",
            "--profile",
            "default",
            "--binding-id",
            "telegram-sales",
            "--scope",
            "auto",
            "--include-global",
            "--json",
        ],
    )
    assert digest_result.exit_code == 0
    digest_payload = json.loads(digest_result.stdout)
    assert digest_payload["local_count"] == 1
    assert digest_payload["global_count"] == 1


def test_skill_cli_crud_and_marketplace(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Top-level skill CLI should manage profile skills and marketplace install/list."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    set_result = runner.invoke(
        app,
        [
            "skill",
            "set",
            "custom-note",
            "--profile",
            "default",
            "--text",
            "# custom-note\n\nUse me.",
        ],
    )
    assert set_result.exit_code == 0
    payload = json.loads(set_result.stdout)
    assert payload["skill"]["name"] == "custom-note"

    show_result = runner.invoke(
        app,
        ["skill", "show", "custom-note", "--profile", "default"],
    )
    assert show_result.exit_code == 0
    assert "Use me." in json.loads(show_result.stdout)["skill"]["content"]

    list_result = runner.invoke(
        app,
        ["skill", "list", "--profile", "default"],
    )
    assert list_result.exit_code == 0
    assert "custom-note" in {item["name"] for item in json.loads(list_result.stdout)["skills"]}

    class _FakeMarketplaceService:
        async def list_source(
            self,
            *,
            source: str,
            limit: int | None = None,
            profile_id: str | None = None,
        ) -> SkillMarketplaceListResult:
            if source == "default":
                source = "skills.sh/openai/skills"
            assert source == "skills.sh/openai/skills"
            assert limit in {None, 50}
            assert profile_id == "default"
            return SkillMarketplaceListResult(
                source=source,
                source_stats=SkillMarketplaceSourceStats(
                    installs_source="skills.sh",
                    total_installs=16_400,
                    total_installs_display="16.4K",
                ),
                items=(
                    SkillMarketplaceListItem(
                        name="research-helper",
                        source=source,
                        path="demo/path",
                        summary="Research helper.",
                        canonical_source="https://raw.githubusercontent.com/openai/skills/main/skills/research-helper/SKILL.md",
                        rank=8,
                        installs=404,
                        installs_display="404",
                        installed=True,
                        installed_name="research-helper",
                        installed_origin="profile",
                    ),
                ),
            )

        async def search_source(
            self,
            *,
            source: str,
            query: str,
            limit: int | None = None,
            profile_id: str | None = None,
        ) -> SkillMarketplaceListResult:
            if source == "default":
                source = "skills.sh/openai/skills"
            assert source == "skills.sh/openai/skills"
            assert query == "research"
            assert limit in {None, 50}
            assert profile_id == "default"
            return SkillMarketplaceListResult(
                source=source,
                items=(
                    SkillMarketplaceListItem(
                        name="research-helper",
                        source=source,
                        path="demo/path",
                        summary="Research helper.",
                        rank=8,
                        installs=404,
                        installs_display="404",
                        installed=True,
                        installed_name="research-helper",
                        installed_origin="profile",
                    ),
                ),
            )

        async def install(
            self,
            *,
            profile_id: str,
            source: str,
            skill: str | None,
            target_name: str | None,
            overwrite: bool,
        ) -> SkillMarketplaceInstallRecord:
            assert profile_id == "default"
            if source == "default":
                source = "skills.sh/openai/skills"
            assert source == "skills.sh/openai/skills"
            assert skill == "research-helper"
            assert target_name == "research-helper-local"
            assert overwrite is True
            return SkillMarketplaceInstallRecord(
                name="research-helper-local",
                path="profiles/default/skills/research-helper-local/SKILL.md",
                source=source,
                summary="Research helper.",
            )

    monkeypatch.setattr(
        "afkbot.cli.commands.skill.get_skill_marketplace_service",
        lambda settings: _FakeMarketplaceService(),
    )

    async def _inspect_profile(*, profile_id: str) -> list[SkillDoctorRecord]:
        assert profile_id == "default"
        return [
            SkillDoctorRecord(
                name="research-helper",
                origin="profile",
                path="profiles/default/skills/research-helper/SKILL.md",
                available=True,
                execution_mode="executable",
                manifest_path="profiles/default/skills/research-helper/AFKBOT.skill.toml",
                manifest_valid=True,
                missing_requirements=(),
                missing_suggested_requirements=(),
                tool_names=("file.read",),
                app_names=(),
                preferred_tool_order=("file.read",),
                suggested_bins=(),
                install_hints=(),
                repair_commands=(),
                issues=(),
            )
        ]

    monkeypatch.setattr(
        "afkbot.cli.commands.skill.get_skill_doctor_service",
        lambda settings: type("_Doctor", (), {"inspect_profile": staticmethod(_inspect_profile)})(),
    )

    # Act
    marketplace_list = runner.invoke(
        app,
        ["skill", "marketplace", "list", "skills.sh/openai/skills"],
    )

    marketplace_install = runner.invoke(
        app,
        [
            "skill",
            "marketplace",
            "install",
            "skills.sh/openai/skills",
            "--profile",
            "default",
            "--skill",
            "research-helper",
            "--target-name",
            "research-helper-local",
            "--overwrite",
        ],
    )

    marketplace_search = runner.invoke(
        app,
        ["skill", "marketplace", "search", "research"],
    )

    marketplace_list_default = runner.invoke(
        app,
        ["skill", "marketplace", "list"],
    )

    doctor_result = runner.invoke(
        app,
        ["skill", "doctor", "--profile", "default"],
    )
    assert doctor_result.exit_code == 0
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["repairs"] == []
    assert doctor_payload["skills"][0]["execution_mode"] == "executable"

    doctor_repair_result = runner.invoke(
        app,
        ["skill", "doctor", "--profile", "default", "--repair-manifests"],
    )
    assert doctor_repair_result.exit_code == 0
    doctor_repair_payload = json.loads(doctor_repair_result.stdout)
    assert doctor_repair_payload["repairs"]
    assert doctor_repair_payload["repairs"][0]["name"] == "custom-note"

    normalize_result = runner.invoke(
        app,
        ["skill", "normalize", "--profile", "default"],
    )
    assert normalize_result.exit_code == 0
    normalize_payload = json.loads(normalize_result.stdout)
    assert normalize_payload["skills"][0]["name"] == "custom-note"
    assert normalize_payload["skills"][0]["action"] in {"skipped", "created", "repaired"}

    repair_result = runner.invoke(
        app,
        ["skill", "repair", "--profile", "default"],
    )
    assert repair_result.exit_code == 0
    repair_payload = json.loads(repair_result.stdout)
    assert repair_payload["skills"][0]["name"] == "custom-note"
    assert repair_payload["skills"][0]["action"] in {"skipped", "created", "repaired"}

    # Assert
    assert marketplace_list.exit_code == 0
    marketplace_list_payload = json.loads(marketplace_list.stdout)
    assert marketplace_list_payload["profile"] == "default"
    assert marketplace_list_payload["resolved_source"] == "skills.sh/openai/skills"
    assert marketplace_list_payload["source_stats"]["total_installs"] == 16_400
    assert marketplace_list_payload["skills"][0]["name"] == "research-helper"
    assert marketplace_list_payload["skills"][0]["installed"] is True
    assert marketplace_list_payload["skills"][0]["rank"] == 8

    assert marketplace_install.exit_code == 0
    assert json.loads(marketplace_install.stdout)["skill"]["name"] == "research-helper-local"

    assert marketplace_search.exit_code == 0
    marketplace_search_payload = json.loads(marketplace_search.stdout)
    assert marketplace_search_payload["skills"][0]["name"] == "research-helper"
    assert marketplace_search_payload["skills"][0]["installs"] == 404

    assert marketplace_list_default.exit_code == 0
    assert json.loads(marketplace_list_default.stdout)["skills"][0]["name"] == "research-helper"

    delete_result = runner.invoke(
        app,
        ["skill", "delete", "custom-note", "--profile", "default"],
    )
    assert delete_result.exit_code == 0
    assert json.loads(delete_result.stdout)["skill"]["name"] == "custom-note"


def test_skill_marketplace_cli_renders_structured_profile_not_found(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Marketplace CLI should return structured profile errors for list/search."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    list_result = runner.invoke(
        app,
        ["skill", "marketplace", "list", "--profile", "missing"],
    )
    search_result = runner.invoke(
        app,
        ["skill", "marketplace", "search", "memory", "--profile", "missing"],
    )

    # Assert
    assert list_result.exit_code == 1
    assert json.loads(list_result.stdout) == {
        "ok": False,
        "error_code": "profile_not_found",
        "reason": "Profile not found: missing",
    }
    assert search_result.exit_code == 1
    assert json.loads(search_result.stdout) == {
        "ok": False,
        "error_code": "profile_not_found",
        "reason": "Profile not found: missing",
    }


def test_subagent_cli_crud_and_runtime(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Top-level subagent CLI should manage descriptors and expose runtime lifecycle commands."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _create_profile(runner)

    set_result = runner.invoke(
        app,
        [
            "subagent",
            "set",
            "analyst",
            "--profile",
            "default",
            "--text",
            "# analyst\n\nFocus on evidence.",
        ],
    )
    assert set_result.exit_code == 0
    assert json.loads(set_result.stdout)["subagent"]["name"] == "analyst"

    show_result = runner.invoke(
        app,
        ["subagent", "show", "analyst", "--profile", "default"],
    )
    assert show_result.exit_code == 0
    assert "Focus on evidence." in json.loads(show_result.stdout)["subagent"]["content"]

    list_result = runner.invoke(
        app,
        ["subagent", "list", "--profile", "default"],
    )
    assert list_result.exit_code == 0
    assert "analyst" in {item["name"] for item in json.loads(list_result.stdout)["subagents"]}

    class _FakeSubagentService:
        async def run(
            self, *, ctx, prompt: str, subagent_name: str | None, timeout_sec: int | None
        ):
            assert ctx.profile_id == "default"
            assert ctx.session_id == "chat:1"
            assert prompt == "Investigate logs"
            assert subagent_name == "analyst"
            assert timeout_sec == 30
            return type(
                "_RunResult",
                (),
                {
                    "model_dump": lambda self, mode="json": {
                        "task_id": "task-1",
                        "status": "running",
                        "subagent_name": "analyst",
                        "timeout_sec": 30,
                    }
                },
            )()

        async def wait(
            self, *, task_id: str, timeout_sec: int | None, profile_id: str, session_id: str
        ):
            assert task_id == "task-1"
            assert timeout_sec == 5
            assert profile_id == "default"
            assert session_id == "chat:1"
            return type(
                "_WaitResult",
                (),
                {
                    "model_dump": lambda self, mode="json": {
                        "task_id": "task-1",
                        "status": "completed",
                        "done": True,
                        "child_session_id": "child:1",
                        "child_run_id": 4,
                    }
                },
            )()

        async def result(self, *, task_id: str, profile_id: str, session_id: str):
            assert task_id == "task-1"
            assert profile_id == "default"
            assert session_id == "chat:1"
            return type(
                "_ResultPayload",
                (),
                {
                    "model_dump": lambda self, mode="json": {
                        "task_id": "task-1",
                        "status": "completed",
                        "output": "Done",
                        "error_code": None,
                        "reason": None,
                    }
                },
            )()

    monkeypatch.setattr(
        "afkbot.cli.commands.subagent.get_subagent_service",
        lambda settings: _FakeSubagentService(),
    )

    run_result = runner.invoke(
        app,
        [
            "subagent",
            "run",
            "--profile",
            "default",
            "--session",
            "chat:1",
            "--name",
            "analyst",
            "--prompt",
            "Investigate logs",
            "--timeout-sec",
            "30",
        ],
    )
    assert run_result.exit_code == 0
    assert json.loads(run_result.stdout)["task"]["task_id"] == "task-1"

    wait_result = runner.invoke(
        app,
        [
            "subagent",
            "wait",
            "task-1",
            "--profile",
            "default",
            "--session",
            "chat:1",
            "--timeout-sec",
            "5",
        ],
    )
    assert wait_result.exit_code == 0
    assert json.loads(wait_result.stdout)["task"]["done"] is True

    result_result = runner.invoke(
        app,
        [
            "subagent",
            "result",
            "task-1",
            "--profile",
            "default",
            "--session",
            "chat:1",
        ],
    )
    assert result_result.exit_code == 0
    assert json.loads(result_result.stdout)["task"]["output"] == "Done"

    delete_result = runner.invoke(
        app,
        ["subagent", "delete", "analyst", "--profile", "default"],
    )
    assert delete_result.exit_code == 0
    assert json.loads(delete_result.stdout)["subagent"]["name"] == "analyst"
