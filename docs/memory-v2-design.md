# AFKBOT Memory V2 Design

## Goal

Improve AFKBOT memory so it is:

- faster on the hot path
- simpler to reason about
- more useful for long-running agents
- easier to observe and evolve

This document is based on the current AFKBOT implementation plus external reference patterns from OpenClaw, Letta, MemGPT/Mem0, and OpenAI agent-memory guidance.

## Current State

AFKBOT already has three separate memory-like mechanisms:

1. Chat history
   - Raw `chat_turn` rows.
   - Recent turns are replayed into the LLM history.

2. Session compaction
   - Older turns are compacted into a trusted summary.
   - The summary is injected as a system message before recent raw turns.

3. Scoped semantic memory
   - `memory_item` rows are stored per `profile/chat/thread/user_in_chat`.
   - `memory.search` retrieves semantically similar items.
   - `memory.upsert` and `memory.promote` persist durable facts and preferences.

The current design is functional, but the boundaries between these layers are not explicit enough.

## What Works Well

- Scope model is strong. `profile/chat/thread/user_in_chat` is better than many agent systems.
- Chat history and semantic memory are already separated in storage.
- Session compaction is incremental and bounded.
- Memory access is policy-aware and fail-closed for user-facing channels.
- Promotion from local memory to profile-global memory already exists.
- The runtime already supports automatic search before a turn and automatic save after finalization.

## Main Problems

### 1. Retrieval quality is limited

The current embedding is a deterministic local hash vector, not a semantic embedding model. This keeps the stack cheap and offline-friendly, but weakens retrieval quality for paraphrases, multilingual queries, and fuzzy recall.

### 2. Retrieval cost grows with memory size

Search currently loads candidate embeddings from SQLite JSON and ranks them in Python. This is simple, but it is still a full candidate scan inside the requested scope.

### 3. The hot path does too much

Today the turn path may include:

- semantic search before the turn
- history replay
- session compaction refresh
- semantic extraction after the turn
- promotion after the turn
- profile garbage collection after each upsert

That is too much work in the synchronous path for a system that should stay responsive.

### 4. Memory types are mixed conceptually

AFKBOT has:

- recent operational context
- durable user/profile facts
- semantic facts learned from chat
- compacted historical summaries

But the runtime mostly exposes only one retrieval-oriented concept: semantic memory. This makes it harder to decide what should always be visible, what should be searched, and what should stay in history only.

### 5. Auto-save is heuristic and narrow

The current extractor is regex-based and operates mainly on the user message. That is predictable, but it misses richer confirmations and can still save noisy items.

## External Reference Patterns

### OpenClaw

Useful ideas:

- canonical human-editable memory (`MEMORY.md`)
- daily operational notes
- retrieval index as a derived store, not the source of truth
- optional background consolidation and "dreaming"
- hybrid retrieval for semantic + exact-token matches

Useful caution:

- markdown-first memory is easy to inspect, but weak as the only long-term memory representation

### Letta

Useful ideas:

- explicit memory hierarchy
- pinned in-context memory blocks
- separate archival semantic memory
- separate conversation recall
- compaction as a first-class layer
- background reflection/consolidation

Useful caution:

- fully self-editing global memory can create memory pollution without a strict policy

### Best-practice synthesis

The most transferable design for AFKBOT is:

- small always-visible memory
- retrieval only when needed
- background writes and consolidation
- explicit separation between profile memory, archival semantic memory, and conversational recall

## Proposed Target Architecture

AFKBOT Memory V2 should use four layers.

### L1. Working Memory

Purpose:
- keep the current turn coherent
- preserve recent task state

Storage:
- existing `chat_turn`
- existing session compaction summary

Runtime behavior:
- keep a short recent tail
- keep one compact trusted session summary
- do not use semantic retrieval for everything

What stays:
- current `ChatHistoryBuilder`
- current `SessionCompactionService`

What changes:
- treat this as working memory only, not long-term memory
- avoid mixing it conceptually with semantic memory

### L2. Core Profile Memory

Purpose:
- keep small durable facts always visible
- remove the need to search for stable preferences every turn

Examples:
- preferred language
- answer style
- stable user identity facts
- hard constraints
- persistent account- or profile-level settings

Storage:
- new structured table or JSON document per profile and optionally per user-in-chat

Runtime behavior:
- inject every turn
- strict size cap
- no semantic search required

Why:
- this is the cheapest and most useful memory tier
- it removes pressure from `memory.search`

### L3. Archival Semantic Memory

Purpose:
- store durable facts, decisions, risks, tasks, and notes that should be searchable but not always pinned

Storage:
- evolve the current `memory_item` model

Runtime behavior:
- queried after a new user message
- filtered by metadata first
- semantic fallback second
- optional global fallback last

What stays:
- current scope model
- current `memory_item` table as the base
- current `memory.upsert`, `memory.search`, `memory.promote`

What changes:
- replace hash embeddings with a real embedding provider behind an interface
- optionally add vector indexing when scale demands it
- change retrieval from "always semantic first" to a retrieval cascade

Recommended retrieval cascade:

1. exact key lookup for known stable fields
2. metadata-filtered scoped candidates
3. recency-biased local matches
4. semantic/vector fallback
5. promoted-global fallback

### L4. Conversation Recall

Purpose:
- answer "when did we discuss this?"
- recover older conversational context without pinning it all into the prompt

Storage:
- raw `chat_turn`
- compacted session summaries

Runtime behavior:
- separate tool and service, for example `conversation.search`
- optimized for historical recall, not semantic profile facts

Why:
- this cleanly separates "durable fact" from "historical transcript evidence"

## What Not To Add Yet

Do not start with graph memory, full multi-hop entity memory, or a large autonomous reflection system.

Those features add complexity before the current bottlenecks are fixed. AFKBOT should first improve:

- memory taxonomy
- retrieval quality
- hot-path latency
- observability

## Write Path Design

Memory writes should be split into synchronous and asynchronous phases.

### Synchronous path

Allowed:

- save the raw turn
- refresh session compaction if needed
- maybe queue a memory-consolidation job

Avoid on the critical path:

- expensive promotion logic
- aggressive dedupe
- global rescoring
- large GC sweeps

### Asynchronous path

Background consolidator responsibilities:

- extract candidate durable memories
- dedupe near-duplicates
- decide local vs promoted-global
- update core profile memory when a fact is stable
- update archival semantic memory
- run retention and trimming

This is the biggest practical change for latency.

## Promotion Policy

Promotion rules must become explicit.

Promote to profile-global only when the memory is:

- durable
- reusable across chats
- safe to retain
- action-relevant
- confirmed or repeated

Keep local-only when the memory is:

- tied to one task or one chat
- temporary
- ambiguous
- not yet confirmed

Recommended additions:

- `confidence`
- `last_confirmed_at`
- `promotion_reason`
- `supersedes_memory_id`
- `stale_at`

## Retrieval Policy

Default turn behavior should be:

1. inject core profile memory every turn
2. build working memory from compacted session + recent turns
3. only query archival semantic memory when the latest user message suggests it is useful
4. only query conversation recall when the task asks for historical evidence or prior discussion

This makes memory cheaper and more predictable.

## Data Model Changes

### Keep

- `memory_item.scope_kind`
- `scope_key`
- `visibility`
- `memory_kind`
- transport selectors

### Add

- `embedding_provider`
- `embedding_model`
- `confidence`
- `last_accessed_at`
- `access_count`
- `superseded_by_id`
- `stale_at`
- `pinned`
- `origin_turn_id`

### New stores

- `profile_memory` for always-visible structured facts
- `conversation_recall_index` or equivalent search layer over old history

## Observability

Memory V2 should emit metrics and traces for:

- retrieval latency by tier
- retrieval hit rate by tier
- injected token count by tier
- write volume by kind and scope
- promotion acceptance rate
- duplicate merge rate
- stale-memory conflict rate
- compaction refresh count
- answer quality with and without memory

Without this, the system will keep getting more complex without proving value.

## Migration Plan

### Phase 1. Clarify the architecture

- introduce the Memory V2 terminology in code and docs
- add a dedicated core/profile memory store
- keep current semantic memory API stable

### Phase 2. Improve retrieval quality

- add pluggable embedding providers
- make real embeddings optional
- add metadata-first retrieval before semantic fallback

### Phase 3. Split archival memory from conversational recall

- add `conversation.search`
- stop overloading semantic memory for transcript recall

### Phase 4. Move write-side work off the hot path

- add a background consolidation worker
- queue extraction, promotion, dedupe, and trim

### Phase 5. Add stronger policies and evals

- define promotion rules per `memory_kind`
- add conflict handling
- add regression evals for retrieval quality and latency

## Concrete Recommendation

If AFKBOT only does one serious memory iteration now, it should do this:

1. Add a small pinned `profile_memory` layer.
2. Keep `chat_turn + session_compaction` as working memory only.
3. Keep `memory_item` as archival semantic memory, but add real embeddings behind an interface.
4. Introduce separate `conversation.search` for historical recall.
5. Move extraction/promotion/dedupe/trim into a background consolidation loop.

That is the highest-value path because it improves:

- speed
- simplicity
- usefulness
- future extensibility

without forcing a full rewrite.

## Summary

AFKBOT does not need "more memory". It needs better memory boundaries.

The winning direction is:

- less magic in the prompt
- smaller always-visible memory
- better scoped retrieval
- separate long-term facts from historical recall
- asynchronous consolidation

That keeps the current strengths of AFKBOT, especially its scope model, while removing the main bottlenecks in latency, retrieval quality, and maintainability.
