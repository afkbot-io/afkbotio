"""Tests for canonical channel delivery target contracts."""

from __future__ import annotations

import pytest

from afkbot.services.channels import ChannelDeliveryTarget, build_delivery_target_runtime_metadata


def test_channel_delivery_target_normalizes_transport_and_metadata() -> None:
    """Delivery target should normalize transport selectors into runtime-safe metadata."""

    target = ChannelDeliveryTarget(
        transport=" Telegram ",
        peer_id=" 42 ",
        thread_id=" 9001 ",
    )

    assert target.transport == "telegram"
    assert build_delivery_target_runtime_metadata(target) == {
        "transport": "telegram",
        "peer_id": "42",
        "thread_id": "9001",
    }


def test_channel_delivery_target_requires_locator() -> None:
    """Transport-only delivery descriptors should be rejected as ambiguous."""

    with pytest.raises(ValueError, match="delivery target requires binding_id or explicit"):
        ChannelDeliveryTarget(transport="telegram")


def test_channel_delivery_target_accepts_chat_id_alias_for_telegram_peer_id() -> None:
    """Telegram-style chat_id should normalize into peer_id for Telegram delivery."""

    target = ChannelDeliveryTarget.model_validate(
        {
            "transport": "telegram",
            "chat_id": "-100200300",
        }
    )

    assert target.peer_id == "-100200300"
    assert target.address is None


def test_channel_delivery_target_accepts_address_alias_for_telegram_peer_id() -> None:
    """Telegram delivery targets may use generic address as one peer_id alias."""

    target = ChannelDeliveryTarget.model_validate(
        {
            "transport": "telegram",
            "address": "-100200300",
        }
    )

    assert target.peer_id == "-100200300"
    assert target.address is None
