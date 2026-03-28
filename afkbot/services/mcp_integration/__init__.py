"""MCP IDE integration module."""

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.ide_adapter import (
    serialize_profile_for_ide,
    serialize_server_for_ide,
)
from afkbot.services.mcp_integration.operator_contracts import (
    MCPAddPreview,
    MCPAddResult,
    MCPServerView,
    MCPValidationReport,
)
from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.mcp_integration.service import (
    MCPProfileService,
    get_mcp_profile_service,
    reset_mcp_profile_services,
)
from afkbot.services.mcp_integration.validator import (
    MCPConfigValidationError,
    validate_server_config,
    validate_server_configs,
)

__all__ = [
    "MCPConfigValidationError",
    "MCPAddPreview",
    "MCPAddResult",
    "MCPProfileLoader",
    "MCPProfileService",
    "MCPServerConfig",
    "MCPServerView",
    "MCPValidationReport",
    "get_mcp_profile_service",
    "reset_mcp_profile_services",
    "serialize_profile_for_ide",
    "serialize_server_for_ide",
    "validate_server_config",
    "validate_server_configs",
]
