"""Graph execution tests for automation graph mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations import AutomationsServiceError
from afkbot.services.automations.graph import executor as graph_executor_module
from afkbot.services.automations.graph.contracts import (
    AutomationGraphNodeSpec,
    AutomationGraphSpec,
    AutomationGraphVersionSpec,
)
from afkbot.services.automations.graph.os_sandbox import CodeNodeLaunch
from afkbot.services.automations.graph.os_sandbox import OSSandboxUnavailableError
from afkbot.services.subagents.contracts import (
    SubagentResultResponse,
    SubagentRunAccepted,
    SubagentWaitResponse,
)
from afkbot.services.subagents.runner import SubagentExecutionResult, SubagentRunner
from afkbot.services.subagents.service import SubagentService
from afkbot.services.tools.base import ToolContext, ToolResult
from afkbot.settings import Settings
from tests.services.automations._harness import FakeLoop, prepare_service


def _prepare_core_researcher(root_dir: Path) -> None:
    path = root_dir / "afkbot/subagents/researcher.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# researcher", encoding="utf-8")


def _prepare_profile_subagent(root_dir: Path, *, profile_id: str, subagent_name: str) -> None:
    path = root_dir / "profiles" / profile_id / "subagents" / f"{subagent_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {subagent_name}", encoding="utf-8")


class _GraphPersistingRunner(SubagentRunner):
    """Deterministic inline runner for graph agent-node tests."""

    async def execute(
        self,
        *,
        session_factory,
        task_id: str,
        profile_id: str,
        parent_session_id: str,
        subagent_name: str,
        subagent_markdown: str,
        prompt: str,
    ) -> SubagentExecutionResult:
        _ = session_factory, profile_id, parent_session_id, subagent_name, subagent_markdown, prompt
        return SubagentExecutionResult(
            output="agent-completed",
            child_session_id=f"child:{task_id}",
            child_run_id=77,
        )


class _TimeoutingSubagentService:
    """Minimal fake service to prove timeout cleanup semantics."""

    def __init__(self) -> None:
        self.cancel_calls: list[dict[str, object]] = []

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> SubagentRunAccepted:
        _ = ctx, prompt
        return SubagentRunAccepted(
            task_id="task-timeout",
            status="running",
            subagent_name=subagent_name or "researcher",
            timeout_sec=timeout_sec or 300,
        )

    async def wait(
        self,
        *,
        task_id: str,
        timeout_sec: int | None,
        profile_id: str,
        session_id: str,
    ) -> SubagentWaitResponse:
        _ = task_id, timeout_sec, profile_id, session_id
        return SubagentWaitResponse(task_id="task-timeout", status="running", done=False)

    async def result(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        _ = task_id, profile_id, session_id
        raise AssertionError("result() must not be called when wait() timed out")

    async def cancel(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        self.cancel_calls.append(
            {
                "task_id": task_id,
                "profile_id": profile_id,
                "session_id": session_id,
            }
        )
        return SubagentResultResponse(
            task_id=task_id,
            status="cancelled",
            error_code="subagent_cancelled",
            reason="Subagent task was cancelled",
        )

    async def shutdown(self) -> None:
        return None


class _FailedAgentSubagentService:
    """Fake service returning one terminal failed child outcome."""

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> SubagentRunAccepted:
        _ = ctx, prompt
        return SubagentRunAccepted(
            task_id="task-failed",
            status="running",
            subagent_name=subagent_name or "researcher",
            timeout_sec=timeout_sec or 300,
        )

    async def wait(
        self,
        *,
        task_id: str,
        timeout_sec: int | None,
        profile_id: str,
        session_id: str,
    ) -> SubagentWaitResponse:
        _ = task_id, timeout_sec, profile_id, session_id
        return SubagentWaitResponse(
            task_id="task-failed",
            status="failed",
            done=True,
            child_session_id="child:task-failed",
            child_run_id=91,
        )

    async def result(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        _ = task_id, profile_id, session_id
        return SubagentResultResponse(
            task_id="task-failed",
            status="failed",
            child_session_id="child:task-failed",
            child_run_id=91,
            error_code="subagent_failed",
            reason="child boom",
        )

    async def cancel(
        self,
        *,
        task_id: str,
        profile_id: str,
        session_id: str,
    ) -> SubagentResultResponse:
        _ = task_id, profile_id, session_id
        raise AssertionError("cancel() must not be called for terminal child failure")

    async def shutdown(self) -> None:
        return None


class _FailingFallbackLoop(FakeLoop):
    """Prompt fallback runner that always raises."""

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides=None,
        **_unused: object,
    ) -> object:
        await super().run_turn(
            profile_id=profile_id,
            session_id=session_id,
            message=message,
            context_overrides=context_overrides,
        )
        raise RuntimeError("fallback exploded")


class _ExplodingSubagentService:
    """Fake service that raises during agent execution to test terminal cleanup."""

    async def run(
        self,
        *,
        ctx: ToolContext,
        prompt: str,
        subagent_name: str | None,
        timeout_sec: int | None,
    ) -> SubagentRunAccepted:
        _ = ctx, prompt, subagent_name, timeout_sec
        raise RuntimeError("subagent factory boom")

    async def wait(self, *, task_id: str, timeout_sec: int | None, profile_id: str, session_id: str):
        _ = task_id, timeout_sec, profile_id, session_id
        raise AssertionError("wait() must not be called after run() failure")

    async def result(self, *, task_id: str, profile_id: str, session_id: str):
        _ = task_id, profile_id, session_id
        raise AssertionError("result() must not be called after run() failure")

    async def cancel(self, *, task_id: str, profile_id: str, session_id: str):
        _ = task_id, profile_id, session_id
        raise AssertionError("cancel() must not be called after run() failure")

    async def shutdown(self) -> None:
        return None


def _code_graph_spec(*, name: str, source_code: str) -> AutomationGraphSpec:
    return AutomationGraphSpec(
        name=name,
        nodes=[
            AutomationGraphNodeSpec(
                key="trigger",
                name="Trigger",
                node_kind="builtin",
                node_type="trigger.input",
            ),
            AutomationGraphNodeSpec(
                key="transform",
                name="Transform",
                node_kind="code",
                node_type="python",
                version=AutomationGraphVersionSpec(
                    runtime="python",
                    source_code=source_code,
                ),
            ),
            AutomationGraphNodeSpec(
                key="finish",
                name="Finish",
                node_kind="builtin",
                node_type="passthrough",
            ),
        ],
        edges=[
            {"source_key": "trigger", "target_key": "transform"},
            {"source_key": "transform", "target_key": "finish"},
        ],
    )


async def test_graph_executor_runs_code_nodes_and_persists_trace(tmp_path) -> None:
    """Graph mode should execute code nodes and persist one deterministic trace ledger."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="code-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="transform",
                        name="Transform",
                        node_kind="code",
                        node_type="python",
                        version=AutomationGraphVersionSpec(
                            runtime="python",
                            source_code=(
                                "def run(context, inputs, config):\n"
                                "    payload = inputs['default']\n"
                                "    return {\n"
                                "        'ports': {\n"
                                "            'default': {\n"
                                "                'value': str(payload['value']).upper(),\n"
                                "                'event_id': payload.get('event_id'),\n"
                                "            }\n"
                                "        }\n"
                                "    }\n"
                            ),
                        ),
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "transform"},
                    {"source_key": "transform", "target_key": "finish"},
                ],
            ),
        )

        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        result = await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-code", "value": "hello"},
        )

        assert result.deduplicated is False
        runs = await service.list_graph_runs(profile_id="default", automation_id=created.id)
        assert len(runs) == 1
        run = runs[0]
        assert run.status == "succeeded"
        assert run.final_output == {"default": {"event_id": "evt-code", "value": "HELLO"}}

        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        assert [item.node_key for item in trace.nodes] == ["trigger", "transform", "finish"]
        assert all(item.status == "succeeded" for item in trace.nodes)
        assert trace.nodes[1].output == {"default": {"event_id": "evt-code", "value": "HELLO"}}
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_network_imports(tmp_path: Path) -> None:
    """Sandboxed code nodes should fail closed on network-capable imports."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-network",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-network",
                source_code=(
                    "import socket\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-network"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox import denied: socket" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_native_socket_imports(tmp_path: Path) -> None:
    """Sandboxed code nodes should also block low-level socket module imports."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-native-socket",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-native-socket",
                source_code=(
                    "import _socket\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-native-socket"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox import denied: _socket" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_importlib_native_loader_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-only sandbox must block `importlib`-based native loader paths."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-importlib-loader",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-importlib-loader",
                source_code=(
                    "import importlib.util\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-importlib-loader"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox import denied: importlib.util" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_raw_file_handles(tmp_path: Path) -> None:
    """Sandboxed code nodes should block raw file APIs exposed through `io.FileIO`."""

    secret_path = tmp_path / "host-secret-raw.txt"
    secret_path.write_text("top-secret", encoding="utf-8")

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-raw-file",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-raw-file",
                source_code=(
                    "import io\n\n"
                    "def run(context, inputs, config):\n"
                    f"    handle = io.FileIO({str(secret_path)!r}, 'r')\n"
                    "    return {'ports': {'default': {'value': handle.read().decode('utf-8')}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-raw-file"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox raw file access denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_symlink_escape_without_os_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python-only sandbox must resolve symlinks before allowing reads inside the tempdir."""

    secret_path = tmp_path / "host-secret-symlink.txt"
    secret_path.write_text("top-secret", encoding="utf-8")
    real_build = graph_executor_module.build_code_node_launch

    def wrapped_build_code_node_launch(**kwargs: object) -> CodeNodeLaunch:
        sandbox_root = Path(kwargs["sandbox_root"])
        (sandbox_root / "alias.txt").symlink_to(secret_path)
        return real_build(**kwargs)

    monkeypatch.setattr(
        graph_executor_module,
        "build_code_node_launch",
        wrapped_build_code_node_launch,
    )
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-symlink-escape",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-symlink-escape",
                source_code=(
                    "from pathlib import Path\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'value': Path('alias.txt').read_text(encoding='utf-8')}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-symlink-escape"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox read denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_os_exec_escape_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-level sandbox must block `os.exec*` even when OS sandboxing is disabled."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-exec-escape",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-exec-escape",
                source_code=(
                    "import os, sys\n\n"
                    "def run(context, inputs, config):\n"
                    "    os.execv(sys.executable, [sys.executable, '-I', '-c', 'print(1)'])\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-exec-escape"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox call denied: os.execv" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_rejects_fd_relative_operations_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-only sandbox must reject fd-relative filesystem APIs instead of misrouting them."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-fd-relative",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-fd-relative",
                source_code=(
                    "import os\n\n"
                    "def run(context, inputs, config):\n"
                    "    fd = os.open('.', os.O_RDONLY)\n"
                    "    try:\n"
                    "        os.unlink('scratch.txt', dir_fd=fd)\n"
                    "    finally:\n"
                    "        os.close(fd)\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-fd-relative"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox fd-relative filesystem access denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_posixsubprocess_import_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-level sandbox must block `_posixsubprocess` imports when OS sandboxing is disabled."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-posixsubprocess",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-posixsubprocess",
                source_code=(
                    "import _posixsubprocess\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-posixsubprocess"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox import denied: _posixsubprocess" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_external_file_reads(tmp_path: Path) -> None:
    """Sandboxed code nodes should not read files outside the worker tempdir/stdlib roots."""

    secret_path = tmp_path / "secret.txt"
    secret_path.write_text("top-secret", encoding="utf-8")

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-fs",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-fs",
                source_code=(
                    "from pathlib import Path\n\n"
                    "def run(context, inputs, config):\n"
                    f"    return Path({str(secret_path)!r}).read_text(encoding='utf-8')\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-fs"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox read denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_hard_link_escape(tmp_path: Path) -> None:
    """Sandboxed code nodes should not hard-link external host files into the tempdir."""

    secret_path = tmp_path / "host-secret.txt"
    secret_path.write_text("top-secret", encoding="utf-8")

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-hard-link",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-hard-link",
                source_code=(
                    "import os\n"
                    "def run(context, inputs, config):\n"
                    f"    os.link({str(secret_path)!r}, 'alias.txt')\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-hard-link"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox link denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_external_directory_listing_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-level sandbox must block metadata/listing side channels when OS sandboxing is disabled."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-listdir",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-listdir",
                source_code=(
                    "import os\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'items': os.listdir('/')}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-listdir"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox read denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_os_access_probe_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-level sandbox must block `os.access` metadata probes when OS sandboxing is disabled."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-access-probe",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-access-probe",
                source_code=(
                    "import os\n\n"
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'readable': os.access('/etc/hosts', os.R_OK)}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-access-probe"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox read denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_denies_os_utime_without_os_sandbox(
    tmp_path: Path,
) -> None:
    """Python-level sandbox must block path-based metadata writes when OS sandboxing is disabled."""

    host_file = tmp_path / "host-metadata-target.txt"
    host_file.write_text("ok", encoding="utf-8")
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-utime",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-utime",
                source_code=(
                    "import os\n\n"
                    "def run(context, inputs, config):\n"
                    f"    os.utime({str(host_file)!r}, None)\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-utime"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_violation"
        assert "Sandbox write denied" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_allows_local_tempdir_io(tmp_path: Path) -> None:
    """Sandboxed code nodes may still use relative tempdir files for local transforms."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-local-io",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-local-io",
                source_code=(
                    "from pathlib import Path\n\n"
                    "def run(context, inputs, config):\n"
                    "    path = Path('scratch.txt')\n"
                    "    path.write_text('ok', encoding='utf-8')\n"
                    "    return {'ports': {'default': {'value': path.read_text(encoding='utf-8')}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-code-local"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "succeeded"
        assert run.final_output == {"default": {"value": "ok"}}
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_passes_explicit_os_sandbox_read_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor should pass explicit sandbox policy roots instead of deriving them from argv."""

    captured: dict[str, object] = {}

    def fake_build_code_node_launch(
        *,
        base_argv: tuple[str, ...],
        sandbox_root: Path,
        explicit_read_roots: tuple[Path, ...],
        settings: Settings,
    ) -> CodeNodeLaunch:
        captured["base_argv"] = base_argv
        captured["sandbox_root"] = sandbox_root
        captured["explicit_read_roots"] = explicit_read_roots
        _ = settings
        return CodeNodeLaunch(argv=base_argv, sandbox_kind="none")

    monkeypatch.setattr(
        graph_executor_module,
        "build_code_node_launch",
        fake_build_code_node_launch,
    )

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-policy-roots",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-policy-roots",
                source_code=(
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-code-policy-roots"},
        )

        explicit_roots = tuple(captured["explicit_read_roots"])
        assert explicit_roots
        assert any(path.name == "graph" for path in explicit_roots)
        assert all(isinstance(path, Path) for path in explicit_roots)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("sandbox_mode", ["auto", "required"])
async def test_graph_executor_code_node_fails_closed_when_os_sandbox_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandbox_mode: str,
) -> None:
    """Enabled OS sandbox modes should fail the code node before execution when unavailable."""

    def fake_build_code_node_launch(**_kwargs: object) -> CodeNodeLaunch:
        raise OSSandboxUnavailableError("OS sandbox is required but unavailable on this host")

    monkeypatch.setattr(
        graph_executor_module,
        "build_code_node_launch",
        fake_build_code_node_launch,
    )
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox=sandbox_mode,
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-required-sandbox",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-required-sandbox",
                source_code=(
                    "def run(context, inputs, config):\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-required-sandbox"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_sandbox_unavailable"
    finally:
        await engine.dispose()


async def test_graph_executor_code_node_enforces_stdio_budget(tmp_path: Path) -> None:
    """Sandboxed code nodes should terminate when worker stdio exceeds the configured budget."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_max_io_bytes=1024,
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-stdio",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=_code_graph_spec(
                name="code-stdio",
                source_code=(
                    "import sys\n\n"
                    "def run(context, inputs, config):\n"
                    "    sys.stdout.write('x' * 5000)\n"
                    "    return {'ports': {'default': {'ok': True}}}\n"
                ),
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError):
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-stdio"},
            )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_resource_limit"
        assert "stdout exceeded" in failed.reason
    finally:
        await engine.dispose()


async def test_graph_executor_task_create_node_creates_task(tmp_path: Path) -> None:
    """Graph runtime should create Task Flow items through explicit task nodes."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-task-create",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="task-create-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="create_task",
                        name="Create Task",
                        node_kind="task",
                        node_type="task.create",
                        config={
                            "title": "Process webhook",
                            "description_path": "default.event_id",
                            "labels": ["automation", "graph"],
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "create_task"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-task-create"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        create_task = next(item for item in trace.nodes if item.node_key == "create_task")
        assert run.status == "succeeded"
        assert create_task.status == "succeeded"
        assert create_task.output is not None
        task_payload = create_task.output["default"]["task"]
        assert task_payload["title"] == "Process webhook"
        assert task_payload["description"] == "evt-task-create"
        assert set(task_payload["labels"]) == {"automation", "graph"}
        assert create_task.effects[0].effect_kind == "task.create"
        assert create_task.effects[0].metadata["task_id"] == task_payload["id"]
    finally:
        await engine.dispose()


async def test_graph_executor_task_create_node_supports_ai_subagent_assignment(
    tmp_path: Path,
) -> None:
    """Graph runtime should create Task Flow items assigned directly to one ai_subagent."""

    _prepare_profile_subagent(tmp_path, profile_id="analyst", subagent_name="researcher")
    engine, factory, service = await prepare_service(tmp_path)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")

        created = await service.create_webhook(
            profile_id="default",
            name="graph-task-create-subagent",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="task-create-subagent-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="create_task",
                        name="Create Task",
                        node_kind="task",
                        node_type="task.create",
                        config={
                            "title": "Process researcher webhook",
                            "description_path": "default.event_id",
                            "owner_type": "ai_subagent",
                            "owner_ref": "analyst:researcher",
                            "labels": ["automation", "subagent"],
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "create_task"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-task-create-subagent"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        create_task = next(item for item in trace.nodes if item.node_key == "create_task")
        assert run.status == "succeeded"
        assert create_task.status == "succeeded"
        assert create_task.output is not None
        task_payload = create_task.output["default"]["task"]
        assert task_payload["title"] == "Process researcher webhook"
        assert task_payload["description"] == "evt-task-create-subagent"
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "analyst:researcher"
        assert set(task_payload["labels"]) == {"automation", "subagent"}
    finally:
        await engine.dispose()


async def test_graph_executor_task_create_node_supports_structured_ai_subagent_assignment(
    tmp_path: Path,
) -> None:
    """Graph runtime should accept structured ai_subagent owner inputs without manual owner_ref."""

    _prepare_profile_subagent(tmp_path, profile_id="analyst", subagent_name="researcher")
    engine, factory, service = await prepare_service(tmp_path)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")

        created = await service.create_webhook(
            profile_id="default",
            name="graph-task-create-structured-subagent",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="task-create-structured-subagent-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="create_task",
                        name="Create Task",
                        node_kind="task",
                        node_type="task.create",
                        config={
                            "title": "Process structured researcher webhook",
                            "description_path": "default.event_id",
                            "owner_profile_id": "analyst",
                            "owner_subagent_name": "researcher",
                            "labels": ["automation", "subagent"],
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "create_task"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-task-create-structured-subagent"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        create_task = next(item for item in trace.nodes if item.node_key == "create_task")
        assert run.status == "succeeded"
        assert create_task.status == "succeeded"
        assert create_task.output is not None
        task_payload = create_task.output["default"]["task"]
        assert task_payload["title"] == "Process structured researcher webhook"
        assert task_payload["description"] == "evt-task-create-structured-subagent"
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "analyst:researcher"
    finally:
        await engine.dispose()


async def test_graph_executor_task_create_uses_automation_principal_when_public_principal_required(
    tmp_path: Path,
) -> None:
    """Strict public-principal mode should still allow graph task creation via automation principal."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        taskflow_public_principal_required=True,
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-task-create-strict-session",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="task-create-strict-session",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="create_task",
                        name="Create Task",
                        node_kind="task",
                        node_type="task.create",
                        config={
                            "title": "Process webhook",
                            "description_path": "default.event_id",
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "create_task"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-task-create-strict-session"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        create_task = next(item for item in trace.nodes if item.node_key == "create_task")
        assert run.status == "succeeded"
        assert create_task.status == "succeeded"
        assert create_task.output is not None
        task_payload = create_task.output["default"]["task"]
        assert task_payload["created_by_type"] == "automation"
        assert task_payload["created_by_ref"] == f"automation:default:{created.id}"
        assert task_payload["last_session_id"] is None
        assert task_payload["last_session_profile_id"] is None
    finally:
        await engine.dispose()


async def test_graph_executor_action_app_run_node_executes_via_app_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graph runtime should execute explicit action nodes through the app runtime seam."""

    class _FakeAppRunTool:
        def parse_params(
            self,
            raw_params: dict[str, object],
            *,
            default_timeout_sec: int,
            max_timeout_sec: int,
        ) -> dict[str, object]:
            assert default_timeout_sec > 0
            assert max_timeout_sec >= default_timeout_sec
            assert raw_params == {
                "app_name": "demo",
                "action": "send",
                "params": {"event_id": "evt-app-run", "static": "ok"},
            }
            return raw_params

        async def execute(self, ctx, params: dict[str, object]) -> ToolResult:
            assert ctx.profile_id == "default"
            assert params["app_name"] == "demo"
            assert params["action"] == "send"
            assert params["params"] == {"event_id": "evt-app-run", "static": "ok"}
            return ToolResult(ok=True, payload={"request_id": "req-demo"})

    monkeypatch.setattr(
        graph_executor_module,
        "_build_app_run_tool",
        lambda _settings: _FakeAppRunTool(),
    )

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-app-run",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="app-run-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="call_app",
                        name="Call App",
                        node_kind="action",
                        node_type="app.run",
                        config={
                            "app_name": "demo",
                            "action": "send",
                            "params": {"static": "ok"},
                            "params_path": "default",
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "call_app"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-app-run"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        call_app = next(item for item in trace.nodes if item.node_key == "call_app")
        assert run.status == "succeeded"
        assert call_app.output == {"default": {"request_id": "req-demo"}}
        assert call_app.effects[0].effect_kind == "app.run"
        assert call_app.effects[0].metadata == {"app_name": "demo", "action": "send"}
    finally:
        await engine.dispose()


async def test_graph_executor_action_tool_run_executes_generic_tool(
    tmp_path: Path,
) -> None:
    """Graph runtime should invoke generic tools through the shared tool registry seam."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-tool-run",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="tool-run-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="call_tool",
                        name="Call Tool",
                        node_kind="action",
                        node_type="tool.run",
                        config={
                            "tool_name": "task.flow.list",
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "call_tool"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-tool-run"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        call_tool = next(item for item in trace.nodes if item.node_key == "call_tool")
        assert run.status == "succeeded"
        assert call_tool.status == "succeeded"
        assert call_tool.output == {"default": {"task_flows": []}}
        assert call_tool.effects[0].effect_kind == "tool.run"
        assert call_tool.effects[0].metadata["tool_name"] == "task.flow.list"
        assert call_tool.effects[0].safety_class == "safe"
    finally:
        await engine.dispose()


async def test_graph_executor_action_tool_run_respects_profile_policy(
    tmp_path: Path,
) -> None:
    """Generic graph tools must still honor the profile policy deny surface."""

    engine, factory, service = await prepare_service(tmp_path)
    try:
        async with session_scope(factory) as session:
            policy = await ProfilePolicyRepository(session).get_or_create_default("default")
            policy.denied_tools_json = json.dumps(
                ["task.flow.list"],
                ensure_ascii=True,
                sort_keys=True,
            )

        created = await service.create_webhook(
            profile_id="default",
            name="graph-tool-run-policy",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="tool-run-policy-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="call_tool",
                        name="Call Tool",
                        node_kind="action",
                        node_type="tool.run",
                        config={
                            "tool_name": "task.flow.list",
                        },
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "call_tool"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-tool-run-policy"},
            )
        assert "Tool is denied by policy: task.flow.list" in exc_info.value.reason

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        call_tool = next(item for item in trace.nodes if item.node_key == "call_tool")
        assert run.status == "failed"
        assert call_tool.status == "failed"
        assert call_tool.error_code == "profile_policy_violation"
    finally:
        await engine.dispose()


async def test_graph_executor_action_tool_run_supports_task_tools_under_automation_principal(
    tmp_path: Path,
) -> None:
    """Generic tool nodes should keep task tools working from strict webhook/cron graph runs."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        taskflow_public_principal_required=True,
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-tool-run-task-comment",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="tool-run-task-comment-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="create_task",
                        name="Create Task",
                        node_kind="task",
                        node_type="task.create",
                        config={
                            "title": "Process webhook",
                            "description_path": "default.event_id",
                        },
                    ),
                    AutomationGraphNodeSpec(
                        key="add_comment",
                        name="Add Comment",
                        node_kind="action",
                        node_type="tool.run",
                        config={
                            "tool_name": "task.comment.add",
                            "params": {
                                "task_id": {"$path": "default.task.id"},
                                "message": "Automation note",
                                "comment_type": "note",
                            },
                        },
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "create_task"},
                    {"source_key": "create_task", "target_key": "add_comment"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-tool-run-task-comment"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        create_task = next(item for item in trace.nodes if item.node_key == "create_task")
        add_comment = next(item for item in trace.nodes if item.node_key == "add_comment")
        assert run.status == "succeeded"
        assert create_task.status == "succeeded"
        assert add_comment.status == "succeeded"
        assert create_task.output is not None
        assert add_comment.output is not None
        task_payload = create_task.output["default"]["task"]
        comment_payload = add_comment.output["default"]["task_comment"]
        assert task_payload["created_by_type"] == "automation"
        assert task_payload["created_by_ref"] == f"automation:default:{created.id}"
        assert comment_payload["actor_type"] == "automation"
        assert comment_payload["actor_ref"] == f"automation:default:{created.id}"
        assert comment_payload["task_id"] == task_payload["id"]
    finally:
        await engine.dispose()


async def test_graph_executor_enforces_code_node_input_schema(tmp_path: Path) -> None:
    """Versioned code nodes should validate inputs before user code executes."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-input-schema",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="code-input-schema",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="transform",
                        name="Transform",
                        node_kind="code",
                        node_type="python",
                        version=AutomationGraphVersionSpec(
                            runtime="python",
                            input_schema={
                                "type": "object",
                                "required": ["default"],
                                "properties": {
                                    "default": {
                                        "type": "object",
                                        "required": ["value"],
                                        "properties": {
                                            "value": {"type": "string"},
                                        },
                                        "additionalProperties": True,
                                    }
                                },
                                "additionalProperties": True,
                            },
                            source_code=(
                                "def run(context, inputs, config):\n"
                                "    return {'ports': {'default': {'ok': True}}}\n"
                            ),
                        ),
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "transform"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-input-schema"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_input_invalid"
        assert "value" in (failed.reason or "")
    finally:
        await engine.dispose()


async def test_graph_executor_enforces_code_node_output_schema(tmp_path: Path) -> None:
    """Versioned code nodes should validate their output contract before fan-out continues."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-code-output-schema",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="code-output-schema",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="transform",
                        name="Transform",
                        node_kind="code",
                        node_type="python",
                        version=AutomationGraphVersionSpec(
                            runtime="python",
                            output_schema={
                                "type": "object",
                                "required": ["default"],
                                "properties": {
                                    "default": {
                                        "type": "object",
                                        "required": ["upper"],
                                        "properties": {
                                            "upper": {"type": "string"},
                                        },
                                        "additionalProperties": True,
                                    }
                                },
                                "additionalProperties": True,
                            },
                            source_code=(
                                "def run(context, inputs, config):\n"
                                "    return {'ports': {'default': {'upper': 42}}}\n"
                            ),
                        ),
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "transform"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-code-output-schema", "value": "hello"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        failed = next(item for item in trace.nodes if item.node_key == "transform")
        assert failed.error_code == "graph_node_output_invalid"
        assert "upper" in (failed.reason or "")
    finally:
        await engine.dispose()


async def test_graph_executor_selects_only_matching_branch(tmp_path) -> None:
    """Switch nodes should execute only the matching downstream branch and skip the rest."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-branch",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="branch-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="router",
                        name="Router",
                        node_kind="builtin",
                        node_type="switch.value",
                        config={"path": "kind", "default_port": "unknown"},
                    ),
                    AutomationGraphNodeSpec(
                        key="billing",
                        name="Billing",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                    AutomationGraphNodeSpec(
                        key="support",
                        name="Support",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "router"},
                    {"source_key": "router", "source_port": "billing", "target_key": "billing"},
                    {"source_key": "router", "source_port": "support", "target_key": "support"},
                ],
            ),
        )

        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        await service.trigger_webhook(
            profile_id="default",
            token=token,
            payload={"event_id": "evt-branch", "kind": "billing", "amount": 42},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        statuses = {item.node_key: item.status for item in trace.nodes}
        assert statuses == {
            "trigger": "succeeded",
            "router": "succeeded",
            "billing": "succeeded",
            "support": "skipped",
        }
        router = next(item for item in trace.nodes if item.node_key == "router")
        assert router.selected_ports == ["billing"]
    finally:
        await engine.dispose()


async def test_graph_executor_records_failure_and_stops_downstream(tmp_path) -> None:
    """A failing node should fail the run and prevent downstream node execution."""

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-failure",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="failure-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "boom"},
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "explode"},
                    {"source_key": "explode", "target_key": "finish"},
                ],
            ),
        )

        assert created.webhook is not None
        token = created.webhook.webhook_token
        assert token is not None

        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=token,
                payload={"event_id": "evt-fail"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "failed"
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        statuses = {item.node_key: item.status for item in trace.nodes}
        assert statuses == {
            "trigger": "succeeded",
            "explode": "failed",
            "finish": "pending",
        }
        failed = next(item for item in trace.nodes if item.node_key == "explode")
        assert failed.error_code == "graph_node_failed"
        assert failed.reason == "boom"
    finally:
        await engine.dispose()


async def test_graph_executor_runs_agent_node_and_persists_trace(tmp_path: Path) -> None:
    """Graph agent nodes should persist child refs and forward their output downstream."""

    _prepare_core_researcher(tmp_path)

    def build_subagent_service(settings: Settings) -> SubagentService:
        return SubagentService(
            settings=settings,
            runner=_GraphPersistingRunner(settings),
            launch_mode="inline",
        )

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent",
            prompt="fallback prompt",
            execution_mode="graph",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-flow",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Summarize payload", "subagent_name": "researcher"},
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "delegate"},
                    {"source_key": "delegate", "target_key": "finish"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-agent"},
        )

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "succeeded"
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        statuses = {item.node_key: item.status for item in trace.nodes}
        assert statuses == {
            "trigger": "succeeded",
            "delegate": "succeeded",
            "finish": "succeeded",
        }
        delegate = next(item for item in trace.nodes if item.node_key == "delegate")
        assert delegate.execution_index == 2
        assert delegate.child_task_id is not None
        assert delegate.child_session_id == f"child:{delegate.child_task_id}"
        assert delegate.child_run_id == 77
        assert [effect.effect_kind for effect in delegate.effects] == ["subagent.run"]
        assert delegate.effects[0].safety_class == "unsafe"
        assert delegate.effects[0].committed is True
        assert delegate.effects[0].idempotency_key is None
        assert delegate.effects[0].metadata["status"] == "completed"
        assert delegate.output == {
            "default": {
                "output": "agent-completed",
                "child_session_id": f"child:{delegate.child_task_id}",
                "child_run_id": 77,
                "task_id": delegate.child_task_id,
            }
        }
    finally:
        await engine.dispose()


async def test_graph_executor_agent_timeout_cancels_child_task(tmp_path: Path) -> None:
    """Timed-out agent nodes should cancel the child task before failing the graph run."""

    fake_service = _TimeoutingSubagentService()

    def build_subagent_service(_settings: Settings) -> _TimeoutingSubagentService:
        return fake_service

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent-timeout",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-timeout",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Summarize payload", "timeout_sec": 1},
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "delegate"},
                    {"source_key": "delegate", "target_key": "finish"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-agent-timeout"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        assert len(fake_service.cancel_calls) == 1
        assert fake_service.cancel_calls[0]["task_id"] == "task-timeout"
        assert fake_service.cancel_calls[0]["profile_id"] == "default"
        session_id = str(fake_service.cancel_calls[0]["session_id"])
        assert session_id.startswith(f"automation-webhook-{created.id}-")

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "failed"
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        statuses = {item.node_key: item.status for item in trace.nodes}
        assert statuses == {
            "trigger": "succeeded",
            "delegate": "failed",
            "finish": "pending",
        }
        delegate = next(item for item in trace.nodes if item.node_key == "delegate")
        assert delegate.error_code == "subagent_timeout"
        assert delegate.child_task_id == "task-timeout"
    finally:
        await engine.dispose()


async def test_graph_executor_marks_run_failed_when_agent_adapter_raises(tmp_path: Path) -> None:
    """Unexpected adapter exceptions should still terminate node and run ledgers cleanly."""

    def build_subagent_service(_settings: Settings) -> _ExplodingSubagentService:
        return _ExplodingSubagentService()

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent-raises",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="fail_closed",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-raises",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Do work"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "delegate"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-agent-raises"},
            )
        assert exc_info.value.error_code == "automation_graph_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "failed"
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        delegate = next(item for item in trace.nodes if item.node_key == "delegate")
        assert delegate.status == "failed"
        assert delegate.error_code == "graph_node_exception"
        assert "subagent factory boom" in delegate.reason
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_if_safe_recovers_after_pre_effect_agent_crash(
    tmp_path: Path,
) -> None:
    """Pre-effect agent crashes should still allow the safe fallback path."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    def build_subagent_service(_settings: Settings) -> _ExplodingSubagentService:
        return _ExplodingSubagentService()

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent-pre-effect-fallback",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai_if_safe",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-pre-effect-fallback",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Do work"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "delegate"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        result = await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-agent-pre-effect"},
            session_runner_factory=session_runner_factory,
        )
        assert result.deduplicated is False
        assert len(fake_loop.calls) == 1

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "fallback_succeeded"
        assert run.fallback_status == "succeeded"
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_invokes_prompt_fallback_with_child_trace(
    tmp_path: Path,
) -> None:
    """Prompt fallback should include child metadata when the fallback mode permits it."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    def build_subagent_service(_settings: Settings) -> _FailedAgentSubagentService:
        return _FailedAgentSubagentService()

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent-fallback",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-failure-fallback",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Summarize payload"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "delegate"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        result = await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-agent-fallback"},
            session_runner_factory=session_runner_factory,
        )
        assert result.deduplicated is False
        assert len(fake_loop.calls) == 1

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "fallback_succeeded"
        assert run.fallback_status == "succeeded"
        assert run.final_output == {"fallback": {"ok": True}}

        message = fake_loop.calls[0]["message"]
        fallback_context = json.loads(message.split("graph_fallback_context=", 1)[1])
        assert fallback_context["graph_status"] == "failed"
        assert fallback_context["graph_error_code"] == "automation_graph_failed"
        delegate = next(item for item in fallback_context["node_trace"] if item["node_key"] == "delegate")
        assert delegate["status"] == "failed"
        assert delegate["error_code"] == "subagent_failed"
        assert delegate["reason"] == "child boom"
        assert delegate["effects"] == [
            {
                "committed": True,
                "effect_kind": "subagent.run",
                "idempotency_key": None,
                "metadata": {"status": "failed"},
                "safety_class": "unsafe",
            }
        ]
        assert delegate["child_task_id"] == "task-failed"
        assert delegate["child_session_id"] == "child:task-failed"
        assert delegate["child_run_id"] == 91
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_if_safe_skips_after_unsafe_side_effects(
    tmp_path: Path,
) -> None:
    """Safe fallback must be skipped once an earlier node already performed unsafe work."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-unsafe-fallback",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai_if_safe",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="unsafe-fallback",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="ai",
                        name="AI",
                        node_kind="ai",
                        node_type="prompt",
                        config={"prompt": "Decide next action"},
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "after-ai"},
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "ai"},
                    {"source_key": "ai", "target_key": "explode"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-unsafe"},
                session_runner_factory=session_runner_factory,
            )
        assert exc_info.value.error_code == "automation_graph_failed"
        assert len(fake_loop.calls) == 1

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "failed"
        assert run.fallback_status == "skipped_unsafe"
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_if_safe_skips_after_committed_agent_failure(
    tmp_path: Path,
) -> None:
    """A committed agent failure must block the safe fallback path."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    def build_subagent_service(_settings: Settings) -> _FailedAgentSubagentService:
        return _FailedAgentSubagentService()

    engine, _, service = await prepare_service(
        tmp_path,
        graph_subagent_service_factory=build_subagent_service,
    )
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-agent-unsafe-fallback",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai_if_safe",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="agent-unsafe-fallback",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="delegate",
                        name="Delegate",
                        node_kind="agent",
                        node_type="subagent.run",
                        config={"prompt": "Summarize payload"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "delegate"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-agent-unsafe"},
                session_runner_factory=session_runner_factory,
            )
        assert exc_info.value.error_code == "automation_graph_failed"
        assert len(fake_loop.calls) == 0

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "failed"
        assert run.fallback_status == "skipped_unsafe"
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_redacts_webhook_payload_in_fallback(
    tmp_path: Path,
) -> None:
    """Fallback prompt packaging should never include raw webhook secrets."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-fallback-redaction",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="fallback-redaction",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "boom"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "explode"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={
                "event_id": "evt-fallback-redact",
                "api_key": "sk-very-secret-token-1234567890",
            },
            session_runner_factory=session_runner_factory,
        )

        assert len(fake_loop.calls) == 1
        message = fake_loop.calls[0]["message"]
        assert "sk-very-secret-token-1234567890" not in message
        assert '"api_key": "[REDACTED]"' in message
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_redacts_effect_metadata_and_keeps_node_outputs(
    tmp_path: Path,
) -> None:
    """Fallback packaging should keep deterministic outputs while redacting effect metadata."""

    fake_loop = FakeLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'automations_service.db'}",
        root_dir=tmp_path,
        automation_graph_code_os_sandbox="disabled",
    )
    engine, _, service = await prepare_service(tmp_path, settings_override=settings)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-fallback-effect-redaction",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="fallback-effect-redaction",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="transform",
                        name="Transform",
                        node_kind="code",
                        node_type="python",
                        version=AutomationGraphVersionSpec(
                            runtime="python",
                            source_code=(
                                "def run(context, inputs, config):\n"
                                "    return {\n"
                                "        'ports': {'default': {'normalized': 'done'}},\n"
                                "        'effects': [\n"
                                "            {\n"
                                "                'effect_kind': 'cache.write',\n"
                                "                'safety_class': 'safe',\n"
                                "                'committed': True,\n"
                                "                'metadata': {'api_key': 'sk-effect-secret-1234567890'},\n"
                                "            }\n"
                                "        ],\n"
                                "    }\n"
                            ),
                        ),
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "boom"},
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "transform"},
                    {"source_key": "transform", "target_key": "explode"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-fallback-effect-redaction"},
            session_runner_factory=session_runner_factory,
        )

        assert len(fake_loop.calls) == 1
        message = fake_loop.calls[0]["message"]
        assert "sk-effect-secret-1234567890" not in message
        fallback_context = json.loads(message.split("graph_fallback_context=", 1)[1])
        transform = next(item for item in fallback_context["node_trace"] if item["node_key"] == "transform")
        assert transform["output"] == {"default": {"normalized": "done"}}
        assert transform["effects"] == [
            {
                "committed": True,
                "effect_kind": "cache.write",
                "idempotency_key": None,
                "metadata": {"api_key": "[REDACTED]"},
                "safety_class": "safe",
            }
        ]

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        trace = await service.get_graph_trace(profile_id="default", run_id=run.id)
        transform_trace = next(item for item in trace.nodes if item.node_key == "transform")
        assert transform_trace.output == {"default": {"normalized": "done"}}
        assert transform_trace.effects[0].metadata["api_key"] == "[REDACTED]"
        assert trace.fallback is not None
        assert trace.fallback.status == "succeeded"
        assert trace.fallback.execution_index == max(item.execution_index or 0 for item in trace.nodes) + 1
    finally:
        await engine.dispose()


async def test_graph_executor_resume_with_ai_redacts_unsafe_action_outputs_in_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback packaging should not leak full outputs from committed unsafe action nodes."""

    fake_loop = FakeLoop()

    class _FakeAppRunTool:
        def parse_params(
            self,
            raw_params: dict[str, object],
            *,
            default_timeout_sec: int,
            max_timeout_sec: int,
        ) -> dict[str, object]:
            _ = default_timeout_sec, max_timeout_sec
            return raw_params

        async def execute(self, ctx, params: dict[str, object]) -> ToolResult:
            _ = ctx, params
            return ToolResult(
                ok=True,
                payload={"message_body": "very sensitive body", "request_id": "req-123"},
            )

    monkeypatch.setattr(
        graph_executor_module,
        "_build_app_run_tool",
        lambda _settings: _FakeAppRunTool(),
    )

    def session_runner_factory(_session_factory, _profile_id: str) -> FakeLoop:
        return fake_loop

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-fallback-unsafe-action-redaction",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="fallback-unsafe-action-redaction",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="call_app",
                        name="Call App",
                        node_kind="action",
                        node_type="app.run",
                        config={"app_name": "demo", "action": "send", "params_path": "default"},
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "boom"},
                    ),
                ],
                edges=[
                    {"source_key": "trigger", "target_key": "call_app"},
                    {"source_key": "call_app", "target_key": "explode"},
                ],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        await service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-unsafe-action-redaction"},
            session_runner_factory=session_runner_factory,
        )

        message = fake_loop.calls[0]["message"]
        assert "very sensitive body" not in message
        fallback_context = json.loads(message.split("graph_fallback_context=", 1)[1])
        call_app = next(item for item in fallback_context["node_trace"] if item["node_key"] == "call_app")
        assert call_app["output_redacted"] is True
        assert call_app["output"] == {
            "redacted": True,
            "ports": ["default"],
            "keys": ["message_body", "request_id"],
        }
    finally:
        await engine.dispose()


async def test_graph_executor_marks_fallback_failure_terminal(tmp_path: Path) -> None:
    """Fallback runner failures should leave the graph run in terminal fallback_failed state."""

    failing_loop = _FailingFallbackLoop()

    def session_runner_factory(_session_factory, _profile_id: str) -> _FailingFallbackLoop:
        return failing_loop

    engine, _, service = await prepare_service(tmp_path)
    try:
        created = await service.create_webhook(
            profile_id="default",
            name="graph-fallback-failure",
            prompt="fallback prompt",
            execution_mode="graph",
            graph_fallback_mode="resume_with_ai",
        )
        await service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="fallback-failure",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="explode",
                        name="Explode",
                        node_kind="builtin",
                        node_type="error.raise",
                        config={"reason": "boom"},
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "explode"}],
            ),
        )

        assert created.webhook is not None
        assert created.webhook.webhook_token is not None
        with pytest.raises(AutomationsServiceError) as exc_info:
            await service.trigger_webhook(
                profile_id="default",
                token=created.webhook.webhook_token,
                payload={"event_id": "evt-fallback-failed"},
                session_runner_factory=session_runner_factory,
            )
        assert exc_info.value.error_code == "automation_graph_fallback_failed"

        run = (await service.list_graph_runs(profile_id="default", automation_id=created.id))[0]
        assert run.status == "fallback_failed"
        assert run.fallback_status == "failed"
        assert run.error_code == "automation_graph_fallback_failed"
    finally:
        await engine.dispose()
