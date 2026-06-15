"""Task Flow Knowledge Spine helpers.

The spine is a compact, canonical set of flow documents used to build bounded
runtime context. It intentionally lives on top of TaskDocument so the first
version does not need a schema migration.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from afkbot.models.task_document import TaskDocument
from afkbot.models.task_flow import TaskFlow

CANONICAL_FLOW_DOCUMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "brief",
        "Project brief",
        (
            "## Project Brief\n\n"
            "Summarize the project goal, target users, constraints, non-goals, and success "
            "criteria. This is the stable intake doc for CTO planning.\n"
        ),
    ),
    (
        "plan",
        "Execution plan",
        (
            "## Execution Plan\n\n"
            "Track the current decomposition, sequencing, dependencies, owners, and validation "
            "path. Keep this focused on what should happen next.\n"
        ),
    ),
    (
        "spec",
        "Specification",
        (
            "## Specification\n\n"
            "Record expected behavior, interfaces, architecture constraints, acceptance "
            "criteria, and important implementation notes.\n"
        ),
    ),
    (
        "decisions",
        "Decisions",
        (
            "## Decisions\n\n"
            "Log durable product, architecture, security, and process decisions with rationale "
            "and the task or evidence that caused them.\n"
        ),
    ),
    (
        "status",
        "Project status",
        (
            "## Project Status\n\n"
            "Maintain the latest project state: completed work, active blockers, review needs, "
            "open risks, and the next CTO action.\n"
        ),
    ),
)

CANONICAL_FLOW_DOCUMENT_KEYS = tuple(item[0] for item in CANONICAL_FLOW_DOCUMENTS)
PLANNING_FLOW_DOCUMENT_KEYS = ("brief", "plan", "spec")
TASK_WORKING_DOCUMENT_KEYS = ("handoff", "notes", "review", "evidence")


@dataclass(frozen=True, slots=True)
class KnowledgePacketDocument:
    """One bounded document excerpt selected for a runtime packet."""

    scope_type: str
    scope_id: str
    document_key: str
    title: str
    revision: int
    confirmation_status: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class KnowledgePacket:
    """Compact project knowledge packet for one task runtime."""

    profile_id: str
    flow_id: str | None
    task_id: str
    context_budget_chars: int
    documents: tuple[KnowledgePacketDocument, ...]
    missing_flow_document_keys: tuple[str, ...]
    unconfirmed_flow_document_keys: tuple[str, ...]
    health_status: str
    ready_for_delegation: bool
    ready_for_execution: bool
    blocking_reasons: tuple[str, ...]
    required_flow_document_keys: tuple[str, ...]


def default_flow_document_body(*, flow: TaskFlow, document_key: str, template: str) -> str:
    """Build a lightweight seed body for one canonical flow document."""

    if document_key == "brief":
        description = str(flow.description or "").strip() or "No description captured yet."
        return (
            "## Project Brief\n\n"
            f"# {flow.title}\n\n"
            f"{description}\n\n"
            "## Success Criteria\n\n"
            "- Define measurable completion criteria before delegating implementation.\n\n"
            "## Constraints\n\n"
            "- Keep project knowledge in canonical Task Flow docs instead of scattered comments.\n"
        )
    return template


def build_knowledge_packet(
    *,
    profile_id: str,
    flow_id: str | None,
    task_id: str,
    flow_documents: Iterable[TaskDocument],
    task_documents: Iterable[TaskDocument],
    context_budget_chars: int = 6000,
) -> KnowledgePacket:
    """Return a bounded packet from canonical flow docs plus task working docs."""

    remaining = max(int(context_budget_chars), 1000)
    packet_documents: list[KnowledgePacketDocument] = []

    canonical_flow_documents = select_canonical_flow_documents(flow_documents)
    task_working_documents = select_task_working_documents(task_documents)
    flow_by_key = {document.document_key: document for document in canonical_flow_documents}
    missing_keys = tuple(
        document_key
        for document_key in CANONICAL_FLOW_DOCUMENT_KEYS
        if document_key not in flow_by_key
    )
    unconfirmed_keys = tuple(
        document_key
        for document_key in CANONICAL_FLOW_DOCUMENT_KEYS
        if (document := flow_by_key.get(document_key)) is not None
        and not _document_is_confirmed(document)
    )
    blocking_reasons = _knowledge_blocking_reasons(
        missing_flow_document_keys=missing_keys,
        unconfirmed_flow_document_keys=unconfirmed_keys,
    )
    ready_for_delegation = not blocking_reasons
    ready_for_execution = ready_for_delegation

    ordered_flow_documents = canonical_flow_documents
    ordered_task_documents = task_working_documents

    for document in (*ordered_flow_documents, *ordered_task_documents):
        if remaining <= 0:
            break
        excerpt = _document_excerpt(document.body, max_chars=min(900, remaining))
        remaining -= len(excerpt)
        packet_documents.append(
            KnowledgePacketDocument(
                scope_type=document.scope_type,
                scope_id=document.scope_id,
                document_key=document.document_key,
                title=document.title,
                revision=int(document.revision or 1),
                confirmation_status=str(document.confirmation_status or "draft"),
                excerpt=excerpt,
            )
        )

    return KnowledgePacket(
        profile_id=profile_id,
        flow_id=flow_id,
        task_id=task_id,
        context_budget_chars=context_budget_chars,
        documents=tuple(packet_documents),
        missing_flow_document_keys=missing_keys,
        unconfirmed_flow_document_keys=unconfirmed_keys,
        health_status="ready" if ready_for_execution else "needs_attention",
        ready_for_delegation=ready_for_delegation,
        ready_for_execution=ready_for_execution,
        blocking_reasons=blocking_reasons,
        required_flow_document_keys=PLANNING_FLOW_DOCUMENT_KEYS,
    )


def select_canonical_flow_documents(documents: Iterable[TaskDocument]) -> tuple[TaskDocument, ...]:
    """Return flow documents allowed by the current Knowledge Spine contract."""

    by_key = {
        document.document_key: document
        for document in documents
        if document.document_key in CANONICAL_FLOW_DOCUMENT_KEYS
    }
    return tuple(
        by_key[document_key]
        for document_key in CANONICAL_FLOW_DOCUMENT_KEYS
        if document_key in by_key
    )


def select_task_working_documents(documents: Iterable[TaskDocument]) -> tuple[TaskDocument, ...]:
    """Return task documents allowed by the current working-doc contract."""

    return tuple(
        sorted(
            (
                document
                for document in documents
                if document.document_key in TASK_WORKING_DOCUMENT_KEYS
            ),
            key=lambda document: (
                TASK_WORKING_DOCUMENT_KEYS.index(document.document_key),
                document.document_key,
            ),
        )
    )


def _document_excerpt(body: str, *, max_chars: int) -> str:
    normalized = " ".join(str(body or "").split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(max_chars - 3, 0)].rstrip()}..."


def _document_is_confirmed(document: TaskDocument) -> bool:
    return (
        str(document.confirmation_status or "").strip() == "confirmed"
        and document.confirmed_revision is not None
        and int(document.confirmed_revision) == int(document.revision or 0)
    )


def _knowledge_blocking_reasons(
    *,
    missing_flow_document_keys: tuple[str, ...],
    unconfirmed_flow_document_keys: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    missing_planning = tuple(
        key for key in PLANNING_FLOW_DOCUMENT_KEYS if key in missing_flow_document_keys
    )
    if missing_planning:
        reasons.append("missing_planning_docs:" + ",".join(missing_planning))
    unconfirmed_planning = tuple(
        key for key in PLANNING_FLOW_DOCUMENT_KEYS if key in unconfirmed_flow_document_keys
    )
    if unconfirmed_planning:
        reasons.append("unconfirmed_planning_docs:" + ",".join(unconfirmed_planning))
    return tuple(reasons)
