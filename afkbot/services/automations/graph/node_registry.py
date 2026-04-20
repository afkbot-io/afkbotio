"""Adapter registry for automation graph node execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from afkbot.services.automations.graph.executor import NodeAdapterResult, NodeInvocation


class GraphNodeAdapter(Protocol):
    """Runtime contract implemented by each node adapter."""

    async def execute(self, invocation: NodeInvocation) -> NodeAdapterResult: ...


class AutomationGraphNodeAdapterRegistry:
    """Lookup table for `(node_kind, node_type)` adapter dispatch."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], GraphNodeAdapter] = {}

    def register(self, *, node_kind: str, node_type: str, adapter: GraphNodeAdapter) -> None:
        """Register one adapter instance for exact node dispatch."""

        self._adapters[(node_kind, node_type)] = adapter

    def get(self, *, node_kind: str, node_type: str) -> GraphNodeAdapter | None:
        """Return one adapter for exact dispatch, if registered."""

        return self._adapters.get((node_kind, node_type))

    def available(self) -> Mapping[tuple[str, str], GraphNodeAdapter]:
        """Expose registered adapters for diagnostics/tests."""

        return dict(self._adapters)
