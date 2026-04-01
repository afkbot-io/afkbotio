"""Shared turn-context helpers for any ingress source."""

from __future__ import annotations

from collections.abc import Mapping

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.ingress.contracts import IngressSelectors


def normalize_ingress_selectors(
    *,
    selectors: IngressSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> IngressSelectors:
    """Return one normalized ingress selector record."""

    if selectors is not None:
        return IngressSelectors(
            transport=_normalize_text(selectors.transport),
            account_id=_normalize_text(selectors.account_id),
            peer_id=_normalize_text(selectors.peer_id),
            thread_id=_normalize_text(selectors.thread_id),
            user_id=_normalize_text(selectors.user_id),
        )
    return IngressSelectors(
        transport=_normalize_text(transport),
        account_id=_normalize_text(account_id),
        peer_id=_normalize_text(peer_id),
        thread_id=_normalize_text(thread_id),
        user_id=_normalize_text(user_id),
    )


def build_ingress_runtime_metadata(
    *,
    selectors: IngressSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    extra_metadata: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Project ingress selectors into trusted runtime metadata."""

    normalized = normalize_ingress_selectors(
        selectors=selectors,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    payload: dict[str, object] = {}
    if normalized.transport is not None:
        payload["transport"] = normalized.transport
    if normalized.account_id is not None:
        payload["account_id"] = normalized.account_id
    if normalized.peer_id is not None:
        payload["peer_id"] = normalized.peer_id
    if normalized.thread_id is not None:
        payload["thread_id"] = normalized.thread_id
    if normalized.user_id is not None:
        payload["user_id"] = normalized.user_id
    if extra_metadata:
        payload.update({str(key): value for key, value in extra_metadata.items()})
    return payload or None


def build_ingress_context_overrides(
    *,
    selectors: IngressSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    extra_metadata: Mapping[str, object] | None = None,
    prompt_overlay: str | None = None,
) -> TurnContextOverrides | None:
    """Build one trusted turn context fragment for ingress selectors."""

    runtime_metadata = build_ingress_runtime_metadata(
        selectors=selectors,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
        extra_metadata=extra_metadata,
    )
    if runtime_metadata is None and not prompt_overlay:
        return None
    return TurnContextOverrides(
        runtime_metadata=runtime_metadata,
        prompt_overlay=prompt_overlay,
    )


def _normalize_text(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None

