"""Serialization helpers for connect-scoped runtime metadata snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.connect.contracts import ConnectAccessTokenContext, ConnectClientMetadata


@dataclass(frozen=True, slots=True)
class ConnectContextSnapshot:
    """Trusted routing snapshot persisted on connect claim/session/access tokens."""

    runtime_metadata: dict[str, object] | None = None
    prompt_overlay: str | None = None

    def to_turn_context_overrides(self) -> TurnContextOverrides | None:
        """Convert persisted connect snapshot into turn-scoped overrides."""

        if self.runtime_metadata is None and self.prompt_overlay is None:
            return None
        return TurnContextOverrides(
            runtime_metadata=self.runtime_metadata,
            prompt_overlay=self.prompt_overlay,
        )


def serialize_runtime_metadata(runtime_metadata: dict[str, object] | None) -> str | None:
    """Encode runtime metadata into stable JSON for DB persistence."""

    if not runtime_metadata:
        return None
    return json.dumps(runtime_metadata, ensure_ascii=True, sort_keys=True)


def deserialize_runtime_metadata(value: str | None) -> dict[str, object] | None:
    """Decode runtime metadata from persisted JSON text."""

    raw = (value or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {str(key): value for key, value in payload.items()}


def snapshot_from_turn_context(
    context_overrides: TurnContextOverrides | None,
) -> ConnectContextSnapshot | None:
    """Project trusted turn overrides into connect-scoped persisted snapshot."""

    if context_overrides is None:
        return None
    runtime_metadata = None
    if context_overrides.runtime_metadata:
        runtime_metadata = {
            str(key): value for key, value in context_overrides.runtime_metadata.items()
        }
    prompt_overlay = (context_overrides.prompt_overlay or "").strip() or None
    if runtime_metadata is None and prompt_overlay is None:
        return None
    return ConnectContextSnapshot(
        runtime_metadata=runtime_metadata,
        prompt_overlay=prompt_overlay,
    )


def snapshot_from_claim_row(claim_row: object) -> ConnectContextSnapshot | None:
    """Project persisted claim row fields into mutable connect snapshot."""

    runtime_metadata = deserialize_runtime_metadata(
        getattr(claim_row, "runtime_metadata_json", None),
    )
    prompt_overlay = (getattr(claim_row, "prompt_overlay", None) or "").strip() or None
    if runtime_metadata is None and prompt_overlay is None:
        return None
    return ConnectContextSnapshot(
        runtime_metadata=runtime_metadata,
        prompt_overlay=prompt_overlay,
    )


def snapshot_from_token_context(
    context: ConnectAccessTokenContext,
) -> ConnectContextSnapshot | None:
    """Project validated access-token context into turn-scoped routing snapshot."""

    if context.runtime_metadata is None and context.prompt_overlay is None:
        return None
    return ConnectContextSnapshot(
        runtime_metadata=context.runtime_metadata,
        prompt_overlay=context.prompt_overlay,
    )


def merge_client_metadata(
    snapshot: ConnectContextSnapshot | None,
    *,
    client: ConnectClientMetadata | None,
) -> ConnectContextSnapshot | None:
    """Merge optional client metadata into persisted connect routing snapshot."""

    if client is None:
        return snapshot
    client_payload = client.serialize()
    if not client_payload:
        return snapshot

    runtime_metadata = {} if snapshot is None or snapshot.runtime_metadata is None else dict(snapshot.runtime_metadata)
    existing_client = runtime_metadata.get("client")
    if isinstance(existing_client, dict):
        merged_client = {str(key): value for key, value in existing_client.items()}
        merged_client.update(client_payload)
    else:
        merged_client = client_payload
    runtime_metadata["client"] = merged_client
    prompt_overlay = None if snapshot is None else snapshot.prompt_overlay
    return ConnectContextSnapshot(
        runtime_metadata=runtime_metadata,
        prompt_overlay=prompt_overlay,
    )
