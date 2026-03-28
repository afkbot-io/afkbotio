"""Tests for MCP profile config validation."""

from __future__ import annotations

import pytest

from afkbot.services.mcp_integration.validator import (
    MCPConfigValidationError,
    validate_server_config,
)


def _valid_payload() -> dict[str, object]:
    return {
        "server": "github",
        "transport": "stdio",
        "capabilities": ["tools", "resources"],
        "env_refs": [{"env_ref": "GITHUB_BASE_URL"}],
        "secret_refs": [{"secret_ref": "github_token"}],
        "enabled": True,
    }


def test_validator_accepts_valid_payload() -> None:
    """Valid MCP payload should parse and normalize to contract model."""

    # Arrange
    payload = _valid_payload()

    # Act
    config = validate_server_config(payload)

    # Assert
    assert config.server == "github"
    assert config.transport == "stdio"
    assert config.capabilities == ("tools", "resources")
    assert config.env_refs[0].env_ref == "GITHUB_BASE_URL"
    assert config.secret_refs[0].secret_ref == "github_token"
    assert config.enabled is True


def test_validator_normalizes_server_ids_to_lowercase() -> None:
    """Valid payloads should canonicalize server ids for later lookup consistency."""

    # Arrange
    payload = _valid_payload()
    payload["server"] = "GitHub"

    # Act
    config = validate_server_config(payload)

    # Assert
    assert config.server == "github"


def test_validator_rejects_missing_required_field() -> None:
    """All contract fields are required by strict validator."""

    # Arrange
    payload = _valid_payload()
    payload.pop("enabled")

    # Act / Assert
    with pytest.raises(MCPConfigValidationError, match="Missing required MCP fields"):
        validate_server_config(payload)


def test_validator_rejects_plaintext_secret_field() -> None:
    """Plaintext secret-like fields are forbidden in MCP profile configs."""

    # Arrange
    payload = _valid_payload()
    payload["token"] = "plaintext"

    # Act / Assert
    with pytest.raises(MCPConfigValidationError, match="Plaintext secret field is forbidden"):
        validate_server_config(payload)


def test_validator_rejects_non_ref_objects_in_secret_refs() -> None:
    """Only {secret_ref} objects are allowed in secret_refs list."""

    # Arrange
    payload = _valid_payload()
    payload["secret_refs"] = ["token-value"]

    # Act / Assert
    with pytest.raises(MCPConfigValidationError, match="secret_refs"):
        validate_server_config(payload)


def test_validator_rejects_invalid_transport_and_capability() -> None:
    """Unknown transport/capability values should fail fast."""

    # Arrange
    payload = _valid_payload()
    payload["transport"] = "pipe"
    payload["capabilities"] = ["tools", "invalid-capability"]

    # Act / Assert
    with pytest.raises(MCPConfigValidationError, match="Unsupported transport"):
        validate_server_config(payload)


def test_validator_accepts_remote_url_for_matching_transport() -> None:
    """Remote MCP URLs should validate when their scheme matches the configured transport."""

    # Arrange
    payload = _valid_payload()
    payload["transport"] = "http"
    payload["url"] = "https://example.com/mcp"

    # Act
    config = validate_server_config(payload)

    # Assert
    assert config.url == "https://example.com/mcp"


def test_validator_rejects_remote_url_scheme_mismatch() -> None:
    """Remote MCP URLs should fail when the scheme does not match the configured transport."""

    # Arrange
    payload = _valid_payload()
    payload["transport"] = "websocket"
    payload["url"] = "https://example.com/mcp"

    # Act / Assert
    with pytest.raises(MCPConfigValidationError, match="websocket transport requires"):
        validate_server_config(payload)
