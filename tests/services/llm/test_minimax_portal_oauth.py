"""Tests for MiniMax Portal OAuth helpers."""

from __future__ import annotations

from afkbot.services.llm.minimax_portal_oauth import (
    MINIMAX_PORTAL_PROVIDER_BASE_URL_CN,
    MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL,
    infer_minimax_portal_region_from_base_url,
    minimax_portal_provider_base_url_for_region,
    normalize_minimax_portal_region,
    normalize_minimax_portal_token_expiry,
    parse_minimax_portal_token_payload,
)


def test_normalize_minimax_portal_region_defaults_to_global() -> None:
    """Unknown MiniMax region values should fall back to global."""

    assert normalize_minimax_portal_region(None) == "global"
    assert normalize_minimax_portal_region("EU") == "global"
    assert normalize_minimax_portal_region("cn") == "cn"


def test_infer_minimax_portal_region_from_base_url_detects_cn_host() -> None:
    """CN MiniMax host should map to cn region."""

    assert infer_minimax_portal_region_from_base_url("https://api.minimaxi.com/v1") == "cn"
    assert infer_minimax_portal_region_from_base_url("https://api.minimax.io/v1") == "global"


def test_minimax_portal_provider_base_url_for_region_is_stable() -> None:
    """Region helper should map to provider-compatible v1 base URLs."""

    assert minimax_portal_provider_base_url_for_region("global") == MINIMAX_PORTAL_PROVIDER_BASE_URL_GLOBAL
    assert minimax_portal_provider_base_url_for_region("cn") == MINIMAX_PORTAL_PROVIDER_BASE_URL_CN


def test_normalize_minimax_portal_token_expiry_supports_ttl_epoch_and_epoch_ms() -> None:
    """Expiry normalizer should support TTL seconds and epoch variants."""

    now = 1_700_000_000
    assert normalize_minimax_portal_token_expiry(3600, now_epoch_sec=now) == now + 3600
    assert normalize_minimax_portal_token_expiry(1_700_000_100, now_epoch_sec=now) == 1_700_000_100
    assert normalize_minimax_portal_token_expiry(1_700_000_100_000, now_epoch_sec=now) == 1_700_000_100


def test_parse_minimax_portal_token_payload_normalizes_fields() -> None:
    """MiniMax OAuth payload parser should return normalized token payload."""

    token = parse_minimax_portal_token_payload(
        {
            "status": "success",
            "access_token": "at-123",
            "refresh_token": "rt-123",
            "expired_in": 3600,
            "resource_url": "https://api.minimax.io/v1",
        },
        now_epoch_sec=1_700_000_000,
    )

    assert token.access_token == "at-123"
    assert token.refresh_token == "rt-123"
    assert token.expires_at_epoch_sec == 1_700_003_600
    assert token.resource_url == "https://api.minimax.io/v1"
