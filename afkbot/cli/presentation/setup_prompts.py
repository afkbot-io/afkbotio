"""Public prompt facade for interactive setup flows."""

from __future__ import annotations

from afkbot.cli.presentation.prompt_i18n import (
    PromptLanguage,
    msg,
    normalize_prompt_language,
)
from afkbot.cli.presentation.setup_policy_prompts import (
    POLICY_PRESET_CHOICES,
    prompt_policy_capabilities,
    prompt_policy_enabled,
    prompt_policy_file_access_mode,
    prompt_policy_network_mode,
    prompt_policy_preset,
    prompt_policy_setup_mode,
    prompt_policy_workspace_scope_mode,
)
from afkbot.cli.presentation.setup_provider_prompts import (
    HTTP_PROXY_TYPE,
    LLM_PROVIDER_CHOICES,
    PROXY_TYPE_CHOICES,
    SOCKS5_PROXY_TYPE,
    SOCKS5H_PROXY_TYPE,
    prompt_chat_model,
    prompt_confirm,
    prompt_custom_interface,
    prompt_certbot_email,
    prompt_nginx_enabled,
    prompt_nginx_https_enabled,
    prompt_nginx_public_host,
    prompt_provider,
    prompt_proxy_config,
    prompt_secret_ack,
    prompt_thinking_level,
)

__all__ = [
    "HTTP_PROXY_TYPE",
    "PromptLanguage",
    "LLM_PROVIDER_CHOICES",
    "POLICY_PRESET_CHOICES",
    "PROXY_TYPE_CHOICES",
    "SOCKS5_PROXY_TYPE",
    "SOCKS5H_PROXY_TYPE",
    "msg",
    "normalize_prompt_language",
    "prompt_chat_model",
    "prompt_confirm",
    "prompt_certbot_email",
    "prompt_custom_interface",
    "prompt_nginx_enabled",
    "prompt_nginx_https_enabled",
    "prompt_nginx_public_host",
    "prompt_policy_capabilities",
    "prompt_policy_enabled",
    "prompt_policy_file_access_mode",
    "prompt_policy_network_mode",
    "prompt_policy_preset",
    "prompt_policy_setup_mode",
    "prompt_policy_workspace_scope_mode",
    "prompt_provider",
    "prompt_proxy_config",
    "prompt_secret_ack",
    "prompt_thinking_level",
]
