"""Application settings and path resolution utilities."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Literal
from collections.abc import Mapping

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from afkbot.browser_backends import BrowserBackendId, DEFAULT_BROWSER_BACKEND
from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.browser_cdp import normalize_browser_cdp_url
from afkbot.services.llm_timeout_policy import (
    DEFAULT_LLM_REQUEST_TIMEOUT_SEC,
    DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
    MAX_LLM_REQUEST_TIMEOUT_SEC,
)


def _package_root() -> Path:
    """Return the package/application root that contains the bundled AFKBOT assets."""

    return Path(__file__).resolve().parents[1]


def _looks_like_source_checkout(path: Path) -> bool:
    """Return whether one directory looks like the editable source checkout."""

    return (path / ".git").exists() and (path / "pyproject.toml").exists()


def _default_runtime_root() -> Path:
    """Return the runtime data root for the active execution mode."""

    package_root = _package_root()
    if _looks_like_source_checkout(package_root):
        return package_root
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "AFKBOT"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AFKBOT"
    data_home = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return data_home / "afkbot"


def _default_app_root() -> Path:
    """Return the packaged application root used for bundled assets."""

    return _package_root()


def _default_runtime_port() -> int:
    """Return the default local runtime port for first-run installs."""

    from afkbot.services.runtime_ports import DEFAULT_EXOTIC_RUNTIME_PORT

    return DEFAULT_EXOTIC_RUNTIME_PORT


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="AFKBOT_", extra="ignore")

    db_url: str = "sqlite+aiosqlite:///./afkbot.db"
    root_dir: Path = Field(default_factory=_default_runtime_root)
    app_dir: Path = Field(default_factory=_default_app_root)
    tool_workspace_root: Path | None = None
    tool_invocation_cwd: Path | None = None
    bootstrap_dir_name: str = "afkbot/bootstrap"
    skills_dir_name: str = "afkbot/skills"
    subagents_dir_name: str = "afkbot/subagents"
    profiles_dir_name: str = "profiles"
    plugins_dir_name: str = "plugins"
    plugins_registry_filename: str = "registry.json"
    bootstrap_files: tuple[str, ...] = ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md")
    tool_timeout_default_sec: int = 15
    tool_timeout_max_sec: int = 120
    agent_tool_parallel_max_concurrent: int = 4
    subagent_timeout_default_sec: int = 900
    subagent_timeout_max_sec: int = 900
    subagent_timeout_grace_sec: int = 5
    subagent_wait_default_sec: int = 5
    subagent_wait_max_sec: int = 300
    subagent_task_ttl_sec: int = 3600
    credentials_master_keys: str | None = None
    mcp_runtime_enabled: bool = True
    mcp_runtime_catalog_ttl_sec: int = 300
    mcp_runtime_discovery_timeout_sec: int = 10
    enabled_tool_plugins: tuple[str, ...] = (
        "app_list",
        "app_run",
        "automation_create",
        "automation_list",
        "automation_get",
        "automation_update",
        "automation_delete",
        "task_board",
        "task_block",
        "task_comment_add",
        "task_comment_list",
        "task_create",
        "task_delegate",
        "task_dependency_add",
        "task_dependency_list",
        "task_dependency_remove",
        "task_event_list",
        "task_flow_create",
        "task_flow_list",
        "task_flow_get",
        "task_list",
        "task_maintenance_sweep",
        "task_get",
        "task_inbox",
        "task_review_list",
        "task_review_approve",
        "task_review_request_changes",
        "task_run_list",
        "task_run_get",
        "task_stale_list",
        "task_update",
        "credentials_create",
        "credentials_update",
        "credentials_delete",
        "credentials_list",
        "credentials_request",
        "file_list",
        "file_read",
        "file_write",
        "file_edit",
        "file_search",
        "diffs_render",
        "bash_exec",
        "browser_control",
        "debug_echo",
        "http_request",
        "web_search",
        "web_fetch",
        "memory_upsert",
        "memory_search",
        "memory_delete",
        "memory_digest",
        "memory_list",
        "memory_promote",
        "mcp_profile_list",
        "mcp_profile_get",
        "mcp_profile_upsert",
        "mcp_profile_delete",
        "mcp_profile_validate",
        "skill_profile_list",
        "skill_profile_get",
        "skill_profile_upsert",
        "skill_profile_delete",
        "skill_marketplace_list",
        "skill_marketplace_search",
        "skill_marketplace_install",
        "session_job_run",
        "subagent_run",
        "subagent_wait",
        "subagent_result",
        "subagent_profile_list",
        "subagent_profile_get",
        "subagent_profile_upsert",
        "subagent_profile_delete",
    )
    chat_human_owner_ref: str | None = None
    llm_provider: Literal[
        "openrouter",
        "openai",
        "openai-codex",
        "claude",
        "moonshot",
        "deepseek",
        "xai",
        "qwen",
        "minimax-portal",
        "github-copilot",
        "custom",
    ] = "openrouter"
    llm_model: str = "minimax/minimax-m2.5"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_codex_api_key: str | None = None
    openai_codex_base_url: str = "https://chatgpt.com/backend-api/codex"
    claude_api_key: str | None = None
    claude_base_url: str = "https://api.anthropic.com/v1"
    moonshot_api_key: str | None = None
    moonshot_base_url: str = "https://api.moonshot.ai/v1"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    xai_api_key: str | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    qwen_api_key: str | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    minimax_portal_api_key: str | None = None
    minimax_portal_base_url: str = "https://api.minimax.io/v1"
    minimax_portal_refresh_token: str | None = None
    minimax_portal_token_expires_at: str | None = None
    minimax_portal_resource_url: str | None = None
    minimax_portal_region: Literal["global", "cn"] | None = None
    github_copilot_api_key: str | None = None
    github_copilot_base_url: str = "https://api.individual.githubcopilot.com"
    custom_api_key: str | None = None
    brave_api_key: str | None = None
    custom_base_url: str = ""
    custom_interface: Literal["openai"] = "openai"
    llm_proxy_type: Literal["none", "http", "socks5", "socks5h"] = "none"
    llm_proxy_url: str | None = None
    llm_debug_diagnostics_enabled: bool = False
    runtime_host: str = "127.0.0.1"
    runtime_port: int = Field(default_factory=_default_runtime_port)
    connect_rate_limit_enabled: bool = True
    connect_claim_rate_limit_window_sec: int = 60
    connect_claim_rate_limit_max_attempts: int = 10
    connect_refresh_rate_limit_window_sec: int = 60
    connect_refresh_rate_limit_max_attempts: int = 20
    connect_revoke_rate_limit_window_sec: int = 60
    connect_revoke_rate_limit_max_attempts: int = 10
    connect_claim_pin_max_attempts: int = 5
    runtime_queue_max_size: int = 100
    runtime_worker_count: int = 4
    runtime_cron_interval_sec: float = 60.0
    runtime_cron_max_due_per_tick: int = 32
    runtime_shutdown_timeout_sec: float = 10.0
    runtime_read_timeout_sec: float = 5.0
    automation_run_timeout_sec: float = 1800.0
    runtime_max_header_bytes: int = 16384
    runtime_max_body_bytes: int = 262144
    taskflow_runtime_poll_interval_sec: float = 5.0
    taskflow_runtime_maintenance_batch_size: int = 32
    taskflow_runtime_claim_ttl_sec: int = 900
    taskflow_runtime_owner_ref: str | None = None
    taskflow_public_principal_required: bool = False
    taskflow_strict_team_profile_ids: bool = False
    taskflow_blocked_revisit_initial_sec: int = 7200
    taskflow_blocked_revisit_max_sec: int = 86400
    browser_backend: BrowserBackendId = DEFAULT_BROWSER_BACKEND
    browser_cdp_url: str | None = None
    browser_lightpanda_binary_path: str | None = None
    browser_lightpanda_disable_telemetry: bool = True
    browser_headless: bool = True
    browser_session_idle_ttl_sec: int = 900
    diffs_artifact_ttl_sec: int = 86400
    nginx_enabled: bool = False
    nginx_port: int = 18080
    public_runtime_url: str | None = None
    public_chat_api_url: str | None = None
    nginx_config_path: str | None = None
    llm_request_timeout_sec: float = DEFAULT_LLM_REQUEST_TIMEOUT_SEC
    llm_shared_request_max_parallel: int = 4
    llm_shared_request_min_interval_ms: int = 1000
    llm_max_iterations: int = DEFAULT_LLM_MAX_ITERATIONS
    llm_thinking_level: Literal["low", "medium", "high", "very_high"] = "medium"
    llm_execution_budget_low_sec: float = Field(default=900.0, gt=0)
    llm_execution_budget_medium_sec: float = Field(default=1800.0, gt=0)
    llm_execution_budget_high_sec: float = Field(default=3600.0, gt=0)
    llm_execution_budget_very_high_sec: float = Field(
        default=DEFAULT_LLM_WALL_CLOCK_BUDGET_SEC,
        gt=0,
    )
    llm_history_turns: int = 8
    chat_planning_mode: Literal["off", "auto", "on"] = "auto"
    chat_secret_guard_enabled: bool = False
    secure_request_ttl_sec: int = 900
    secure_flow_max_steps: int = 10
    memory_retention_days: int = 180
    memory_max_items_per_profile: int = 5000
    memory_gc_batch_size: int = 500
    memory_core_enabled: bool = False
    memory_core_max_items: int = 8
    memory_core_max_chars: int = 600
    memory_auto_search_enabled: bool = False
    memory_auto_search_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] = (
        "auto"
    )
    memory_auto_search_limit: int = 3
    memory_auto_search_include_global: bool = True
    memory_auto_search_chat_limit: int = 3
    memory_auto_search_global_limit: int = 2
    memory_global_fallback_enabled: bool = True
    memory_auto_context_item_chars: int = 240
    memory_auto_save_enabled: bool = False
    memory_auto_save_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] = (
        "auto"
    )
    memory_auto_promote_enabled: bool = False
    memory_auto_save_kinds: tuple[str, ...] = (
        "fact",
        "preference",
        "decision",
        "task",
        "risk",
        "note",
    )
    memory_auto_save_max_chars: int = 1000
    memory_recall_enabled: bool = False
    session_compaction_enabled: bool = False
    session_compaction_trigger_turns: int = 12
    session_compaction_keep_recent_turns: int = 6
    session_compaction_max_chars: int = 4000
    session_compaction_prune_raw_turns: bool = False
    channel_routing_fallback_transports: tuple[str, ...] = ("cli", "api", "automation")
    channel_routing_telemetry_enabled: bool = True
    channel_routing_telemetry_history_size: int = 200
    telegram_polling_limit: int = 20
    telegram_polling_timeout_sec: int = 20
    telegram_polling_idle_sleep_ms: int = 250
    telegram_polling_error_backoff_ms: int = 1000
    enable_profile_app_modules: bool = False
    cli_progress_poll_interval_ms: int = 150
    cli_progress_batch_size: int = 50
    skip_setup_guard: bool = False
    setup_state_relpath: str = "profiles/.system/setup_state.json"
    runtime_config_relpath: str = "profiles/.system/runtime_config.json"
    runtime_secrets_relpath: str = "profiles/.system/runtime_secrets.json"
    runtime_secrets_key_relpath: str = "profiles/.system/runtime_secrets.key"
    skills_marketplace_skills_sh_hosts: tuple[str, ...] = ("skills.sh", "www.skills.sh")
    skills_marketplace_github_hosts: tuple[str, ...] = ("github.com", "www.github.com")
    skills_marketplace_raw_github_hosts: tuple[str, ...] = ("raw.githubusercontent.com",)
    skills_marketplace_default_source: str = "skills.sh/openai/skills"
    skills_marketplace_default_refs: tuple[str, ...] = ("main", "master")
    skills_marketplace_default_base_paths: tuple[str, ...] = ("skills/.curated", "skills", "")
    skills_marketplace_max_markdown_bytes: int = 400_000
    skills_marketplace_max_json_bytes: int = 2_000_000
    skills_marketplace_timeout_sec: int = 15
    skills_marketplace_user_agent: str = "afkbot/skills-marketplace"

    @field_validator(
        "llm_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "openai_codex_api_key",
        "claude_api_key",
        "moonshot_api_key",
        "deepseek_api_key",
        "xai_api_key",
        "qwen_api_key",
        "minimax_portal_api_key",
        "minimax_portal_refresh_token",
        "github_copilot_api_key",
        "custom_api_key",
        "brave_api_key",
        mode="before",
    )
    @classmethod
    def _normalize_api_key_value(cls, value: str | None) -> str | None:
        """Treat empty API key values from env files as an absent key."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator(
        "minimax_portal_token_expires_at", "minimax_portal_resource_url", mode="before"
    )
    @classmethod
    def _normalize_optional_secret_metadata_text(cls, value: str | None) -> str | None:
        """Normalize optional profile-local secret metadata text values."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("minimax_portal_region", mode="before")
    @classmethod
    def _normalize_minimax_portal_region(cls, value: str | None) -> str | None:
        """Normalize optional MiniMax region values before enum validation."""

        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    @field_validator(
        "llm_base_url",
        "public_runtime_url",
        "public_chat_api_url",
        "nginx_config_path",
        "browser_lightpanda_binary_path",
        mode="before",
    )
    @classmethod
    def _normalize_base_url_value(cls, value: str | None) -> str | None:
        """Treat empty optional URL/path-like values as absent overrides."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("browser_cdp_url", mode="before")
    @classmethod
    def _normalize_browser_cdp_url(cls, value: str | None) -> str | None:
        """Normalize optional browser CDP URLs, including shorthand host:port forms."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalize_browser_cdp_url(normalized)

    @field_validator("llm_proxy_url", mode="before")
    @classmethod
    def _normalize_llm_proxy_url(cls, value: str | None) -> str | None:
        """Treat empty proxy URL values from env files as an absent value."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("tool_workspace_root", "tool_invocation_cwd", mode="before")
    @classmethod
    def _normalize_tool_workspace_path_override(cls, value: object) -> object:
        """Treat empty tool workspace path overrides as disabled."""

        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("taskflow_runtime_owner_ref", mode="before")
    @classmethod
    def _normalize_taskflow_runtime_owner_ref(cls, value: object) -> str | None:
        """Normalize optional detached runtime owner filter."""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > 255:
            raise ValueError("taskflow_runtime_owner_ref must be <= 255 characters")
        return text

    @field_validator(
        "runtime_queue_max_size",
        "connect_claim_rate_limit_window_sec",
        "connect_claim_rate_limit_max_attempts",
        "connect_refresh_rate_limit_window_sec",
        "connect_refresh_rate_limit_max_attempts",
        "connect_revoke_rate_limit_window_sec",
        "connect_revoke_rate_limit_max_attempts",
        "connect_claim_pin_max_attempts",
        "runtime_worker_count",
        "agent_tool_parallel_max_concurrent",
        "runtime_cron_max_due_per_tick",
        "runtime_max_header_bytes",
        "runtime_max_body_bytes",
        "taskflow_runtime_maintenance_batch_size",
        "taskflow_runtime_claim_ttl_sec",
        "taskflow_blocked_revisit_initial_sec",
        "taskflow_blocked_revisit_max_sec",
        "browser_session_idle_ttl_sec",
        "diffs_artifact_ttl_sec",
        "secure_flow_max_steps",
        "session_compaction_trigger_turns",
        "session_compaction_keep_recent_turns",
        "session_compaction_max_chars",
        "channel_routing_telemetry_history_size",
        "telegram_polling_limit",
        "telegram_polling_timeout_sec",
        "telegram_polling_idle_sleep_ms",
        "telegram_polling_error_backoff_ms",
        "skills_marketplace_max_markdown_bytes",
        "skills_marketplace_max_json_bytes",
        "skills_marketplace_timeout_sec",
        "llm_shared_request_max_parallel",
    )
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        """Validate runtime integer limits that must be strictly positive."""

        if value < 1:
            raise ValueError("runtime limits must be >= 1")
        return value

    @field_validator("llm_shared_request_min_interval_ms")
    @classmethod
    def _validate_non_negative_interval(cls, value: int) -> int:
        """Validate shared LLM pacing interval."""

        if value < 0:
            raise ValueError("runtime intervals must be >= 0")
        return value

    @field_validator(
        "memory_retention_days",
        "memory_max_items_per_profile",
        "memory_gc_batch_size",
        "memory_core_max_items",
        "memory_core_max_chars",
        "memory_auto_search_limit",
        "memory_auto_search_chat_limit",
        "memory_auto_search_global_limit",
        "memory_auto_context_item_chars",
        "memory_auto_save_max_chars",
    )
    @classmethod
    def _validate_positive_memory_limits(cls, value: int) -> int:
        """Validate memory policy limits that must be strictly positive."""

        if value < 1:
            raise ValueError("memory limits must be >= 1")
        return value

    @field_validator("llm_history_turns")
    @classmethod
    def _validate_non_negative_history_turns(cls, value: int) -> int:
        """Validate number of replayed history turns."""

        if value < 0:
            raise ValueError("llm_history_turns must be >= 0")
        return value

    @field_validator("channel_routing_fallback_transports", mode="before")
    @classmethod
    def _normalize_channel_routing_fallback_transports(cls, value: object) -> object:
        """Normalize fallback transport policy to a lowercase tuple."""

        if value is None:
            return ("cli", "api", "automation")
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        if isinstance(value, Mapping):
            return value
        if isinstance(value, (list, tuple, set, frozenset)):
            return tuple(str(item).strip().lower() for item in value if str(item).strip())
        return value

    @model_validator(mode="before")
    @classmethod
    def _resolve_root_and_app_dirs(cls, value: object) -> object:
        """Resolve runtime and application roots before field validation."""

        if value is None:
            data: dict[str, object] = {}
        elif isinstance(value, Mapping):
            data = dict(value)
        else:
            return value

        root_dir = data.get("root_dir")
        app_dir = data.get("app_dir")
        root_is_configured = root_dir not in {None, ""}
        app_is_configured = app_dir not in {None, ""}

        # Preserve source-checkout semantics for tests and local development:
        # when callers relocate the runtime root inside a checkout, bundled
        # assets are still expected under that same tree.
        if (
            root_is_configured
            and not app_is_configured
            and _looks_like_source_checkout(_package_root())
        ):
            data["app_dir"] = root_dir
        return data

    @field_validator(
        "runtime_cron_interval_sec",
        "runtime_read_timeout_sec",
        "automation_run_timeout_sec",
        "taskflow_runtime_poll_interval_sec",
        "llm_request_timeout_sec",
    )
    @classmethod
    def _validate_positive_float(cls, value: float) -> float:
        """Validate runtime intervals that must be strictly positive."""

        if value <= 0:
            raise ValueError("runtime intervals must be > 0")
        return value

    @field_validator("llm_request_timeout_sec")
    @classmethod
    def _validate_llm_request_timeout_cap(cls, value: float) -> float:
        """Validate configured LLM timeout upper bound."""

        if value > MAX_LLM_REQUEST_TIMEOUT_SEC:
            raise ValueError(
                f"llm_request_timeout_sec must be <= {int(MAX_LLM_REQUEST_TIMEOUT_SEC)}"
            )
        return value

    @field_validator("runtime_shutdown_timeout_sec")
    @classmethod
    def _validate_non_negative_float(cls, value: float) -> float:
        """Validate runtime shutdown timeout lower bound."""

        if value < 0:
            raise ValueError("runtime_shutdown_timeout_sec must be >= 0")
        return value

    @model_validator(mode="after")
    def _resolve_relative_sqlite_db_url(self) -> Settings:
        """Resolve default relative SQLite URLs under the configured runtime root."""

        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if not self.db_url.startswith(prefix):
                continue
            relative_path = self.db_url[len(prefix) :]
            if relative_path == ":memory:" or relative_path.startswith("/"):
                break
            resolved = (self.root_dir / relative_path).resolve(strict=False)
            self.db_url = f"{prefix}{resolved}"
            break
        if self.memory_recall_enabled and "memory_recall_search" not in self.enabled_tool_plugins:
            self.enabled_tool_plugins = (*self.enabled_tool_plugins, "memory_recall_search")
        return self

    @property
    def bootstrap_dir(self) -> Path:
        """Return absolute bootstrap directory path."""

        return self.app_dir / self.bootstrap_dir_name

    @property
    def skills_dir(self) -> Path:
        """Return absolute core skills directory path."""

        return self.app_dir / self.skills_dir_name

    @property
    def subagents_dir(self) -> Path:
        """Return absolute core subagents directory path."""

        return self.app_dir / self.subagents_dir_name

    @property
    def profiles_dir(self) -> Path:
        """Return absolute profiles directory path."""

        return self.root_dir / self.profiles_dir_name

    @property
    def plugins_dir(self) -> Path:
        """Return absolute runtime plugins directory path."""

        return self.root_dir / self.plugins_dir_name

    @property
    def plugins_packages_dir(self) -> Path:
        """Return absolute installed plugin packages directory path."""

        return self.plugins_dir / "packages"

    @property
    def plugins_config_dir(self) -> Path:
        """Return absolute plugin config directory path."""

        return self.plugins_dir / "config"

    @property
    def plugins_data_dir(self) -> Path:
        """Return absolute plugin data directory path."""

        return self.plugins_dir / "data"

    @property
    def plugins_registry_path(self) -> Path:
        """Return absolute plugin registry JSON path."""

        return self.plugins_dir / self.plugins_registry_filename

    @property
    def tool_workspace_dir(self) -> Path:
        """Return effective filesystem workspace root for file/shell tools."""

        override = self.tool_workspace_root
        if override is None:
            return self.root_dir.resolve()
        candidate = override
        if not candidate.is_absolute():
            candidate = self.root_dir / candidate
        return candidate.resolve(strict=False)

    @property
    def setup_state_path(self) -> Path:
        """Return absolute path to setup state marker."""

        return self.root_dir / self.setup_state_relpath

    @property
    def runtime_config_path(self) -> Path:
        """Return absolute path to persisted runtime config."""

        return self.root_dir / self.runtime_config_relpath

    @property
    def runtime_secrets_path(self) -> Path:
        """Return absolute path to persisted runtime secrets."""

        return self.root_dir / self.runtime_secrets_relpath

    @property
    def runtime_secrets_key_path(self) -> Path:
        """Return absolute path to runtime-secrets encryption key."""

        return self.root_dir / self.runtime_secrets_key_relpath

    @property
    def diffs_artifacts_dir(self) -> Path:
        """Return absolute path to persisted diff artifacts."""

        return self.profiles_dir / ".system" / "artifacts" / "diffs"


_ENV_PREFIX = "AFKBOT_"
_RUNTIME_SECRET_FIELD_NAMES = frozenset(
    {
        "credentials_master_keys",
        "llm_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "openai_codex_api_key",
        "claude_api_key",
        "moonshot_api_key",
        "deepseek_api_key",
        "xai_api_key",
        "qwen_api_key",
        "minimax_portal_api_key",
        "github_copilot_api_key",
        "custom_api_key",
        "brave_api_key",
    }
)
_RUNTIME_CONFIG_FIELD_NAMES = frozenset(
    field_name
    for field_name in Settings.model_fields.keys()
    if field_name not in _RUNTIME_SECRET_FIELD_NAMES
    and field_name != "root_dir"
    and not field_name.endswith("_dir_name")
    and not field_name.endswith("_relpath")
)


def _has_explicit_env_override(field_name: str) -> bool:
    """Return True when environment explicitly sets a non-empty value for field."""

    env_name = f"{_ENV_PREFIX}{field_name.upper()}"
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return False
    return bool(raw_value.strip())


def _apply_runtime_overrides(
    *,
    merged: dict[str, object],
    payload: Mapping[str, object],
    allowed_fields: frozenset[str],
) -> None:
    """Merge runtime store payload into settings dict under deterministic constraints."""

    for key, value in payload.items():
        if key not in allowed_fields:
            continue
        if key not in merged:
            continue
        if _has_explicit_env_override(key):
            continue
        merged[key] = value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""

    seeded = Settings()
    try:
        from afkbot.services.setup.runtime_store import read_runtime_config, read_runtime_secrets
    except Exception:
        return seeded

    config = read_runtime_config(seeded)
    secrets = read_runtime_secrets(seeded)
    if not config and not secrets:
        return seeded

    merged = seeded.model_dump()
    _apply_runtime_overrides(
        merged=merged,
        payload=config,
        allowed_fields=_RUNTIME_CONFIG_FIELD_NAMES,
    )
    _apply_runtime_overrides(
        merged=merged,
        payload=secrets,
        allowed_fields=_RUNTIME_SECRET_FIELD_NAMES,
    )
    return Settings(**merged)
