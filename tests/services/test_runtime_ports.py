"""Tests for runtime health probe compatibility helpers."""

from __future__ import annotations

import json

from afkbot.services.runtime_ports import probe_runtime_stack


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_probe_runtime_stack_accepts_legacy_health_payloads(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Rolling upgrades should still recognize 1.0.12 runtime/API health payloads."""

    responses = iter(
        [
            _FakeResponse({"ok": True}),
            _FakeResponse({"status": "ok"}),
            _FakeResponse({"ok": True}),
            _FakeResponse({"status": "ready"}),
        ]
    )
    monkeypatch.setattr(
        "afkbot.services.runtime_ports.urlopen",
        lambda url, timeout=1.0: next(responses),
    )

    probe = probe_runtime_stack(host="127.0.0.1", runtime_port=18080)

    assert probe.runtime.ok is True
    assert probe.api.ok is True
    assert probe.running is True


def test_probe_runtime_stack_rejects_generic_non_afkbot_health_pair(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Generic health endpoints without AFKBOT service markers should not count as AFKBOT."""

    responses = iter(
        [
            _FakeResponse({"ok": True}),
            _FakeResponse({"status": "ok"}),
            _FakeResponse({"ok": False}),
            _FakeResponse({"status": "busy"}),
        ]
    )
    monkeypatch.setattr(
        "afkbot.services.runtime_ports.urlopen",
        lambda url, timeout=1.0: next(responses),
    )

    probe = probe_runtime_stack(host="127.0.0.1", runtime_port=18080)

    assert probe.running is False
    assert probe.runtime.ok is False
    assert probe.api.ok is False
