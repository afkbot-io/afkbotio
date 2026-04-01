"""Transport-agnostic ingress selector contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IngressSelectors:
    """Normalized selectors carried by one ingress request."""

    transport: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None

