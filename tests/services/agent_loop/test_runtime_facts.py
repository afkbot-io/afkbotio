"""Tests for trusted runtime fact collection."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.agent_loop.runtime_facts import TrustedRuntimeFactsService
from afkbot.settings import Settings


async def test_trusted_runtime_facts_service_renders_detected_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Runtime facts block should render trusted host details for the prompt."""

    # Arrange
    (tmp_path / ".git").mkdir()
    settings = Settings(root_dir=tmp_path)
    service = TrustedRuntimeFactsService(settings=settings)

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.system",
        lambda: "Linux",
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.machine",
        lambda: "arm64",
    )
    monkeypatch.setattr(
        TrustedRuntimeFactsService,
        "_read_linux_distro",
        staticmethod(lambda: "Ubuntu 24.04"),
    )
    monkeypatch.setattr(
        TrustedRuntimeFactsService,
        "_detect_is_root",
        staticmethod(lambda: False),
    )

    def _which(name: str) -> str | None:
        mapping = {
            "sudo": "/usr/bin/sudo",
            "systemctl": "/usr/bin/systemctl",
            "apt": "/usr/bin/apt",
        }
        return mapping.get(name)

    monkeypatch.setattr("afkbot.services.agent_loop.runtime_facts.shutil.which", _which)

    # Act
    block = await service.build_prompt_block(profile_id="default")

    # Assert
    assert "Trusted runtime facts for this AFKBOT process." in block
    assert f"- workspace_root: {tmp_path / 'profiles/default'}" in block
    assert f"- repo_root: {tmp_path}" in block
    assert "- execution_target: local_runtime" in block
    assert (
        "- current_host_scope: shell and file actions apply to this current AFKBOT runtime host "
        "and allowed workspace"
    ) in block
    assert "- os: linux" in block
    assert "- distro: Ubuntu 24.04" in block
    assert "- arch: arm64" in block
    assert "- shell: /bin/zsh" in block
    assert "- system_locale: ru_RU.UTF-8" in block
    assert "- prompt_language: ru" in block
    assert "- is_root: no" in block
    assert "- has_sudo: yes" in block
    assert "- has_systemctl: yes" in block
    assert "- package_managers: apt" in block
    assert "- Prefer prompt_language for default responses unless the user clearly asked for another language." in block
    assert "- This session is already a valid execution environment for the current host and workspace above." in block
    assert (
        "- If shell or file tools are visible and policy allows, execute current-host tasks here "
        "instead of turning them into manual instructions."
    ) in block
    assert (
        "- Another host or service mentioned by the user is not a blocker by itself. "
        "Inspect what this environment can actually reach before concluding that extra access "
        "is required."
    ) in block


async def test_trusted_runtime_facts_service_handles_missing_optional_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Runtime facts block should stay usable when optional host details are unavailable."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    service = TrustedRuntimeFactsService(settings=settings)

    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setattr(
        "afkbot.cli.presentation.prompt_i18n.locale.getlocale",
        lambda: (None, None),
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.machine",
        lambda: "x86_64",
    )
    monkeypatch.setattr(
        TrustedRuntimeFactsService,
        "_detect_is_root",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr("afkbot.services.agent_loop.runtime_facts.shutil.which", lambda name: None)

    # Act
    block = await service.build_prompt_block(profile_id="default")

    # Assert
    assert "- repo_root: not detected" in block
    assert "- execution_target: local_runtime" in block
    assert "- distro: unknown" in block
    assert "- shell: unknown" in block
    assert "- system_locale: unknown" in block
    assert "- prompt_language: en" in block
    assert "- is_root: unknown" in block
    assert "- has_sudo: no" in block
    assert "- has_systemctl: no" in block
    assert "- package_managers: none detected" in block


async def test_trusted_runtime_facts_service_caches_process_stable_host_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Runtime facts service should probe process-stable host details only once per instance."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    service = TrustedRuntimeFactsService(settings=settings)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.system",
        lambda: "Linux",
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_facts.platform.machine",
        lambda: "x86_64",
    )
    monkeypatch.setattr(
        TrustedRuntimeFactsService,
        "_read_linux_distro",
        staticmethod(lambda: "Ubuntu 24.04"),
    )
    monkeypatch.setattr(
        TrustedRuntimeFactsService,
        "_detect_is_root",
        staticmethod(lambda: False),
    )

    calls: list[str] = []

    def _which(name: str) -> str | None:
        calls.append(name)
        mapping = {
            "sudo": "/usr/bin/sudo",
            "systemctl": "/usr/bin/systemctl",
            "apt": "/usr/bin/apt",
        }
        return mapping.get(name)

    monkeypatch.setattr("afkbot.services.agent_loop.runtime_facts.shutil.which", _which)

    # Act
    await service.build_prompt_block(profile_id="default")
    await service.build_prompt_block(profile_id="default")

    # Assert
    assert calls == ["apt", "apt-get", "dnf", "yum", "brew", "pacman", "sudo", "systemctl"]
