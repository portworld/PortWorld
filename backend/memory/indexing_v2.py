from __future__ import annotations

from typing import Iterable

from backend.core.storage import now_ms
from backend.memory.normalization_v2 import (
    normalize_retrieval_index_state,
    normalize_semantic_key,
    normalize_tags,
)
from backend.memory.types_v2 import MemoryItem, RetrievalIndexEntry, RetrievalIndexState


def filter_memory_items(
    items: Iterable[MemoryItem],
    *,
    scope: str | None = None,
    memory_class: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    session_id: str | None = None,
) -> list[MemoryItem]:
    normalized_tag = next(iter(normalize_tags([tag])), None) if tag else None
    filtered: list[MemoryItem] = []
    for item in items:
        if scope and item.scope != scope:
            continue
        if memory_class and item.memory_class != memory_class:
            continue
        if status and item.status != status:
            continue
        if normalized_tag and normalized_tag not in item.tags:
            continue
        if session_id and item.session_id != session_id:
            continue
        filtered.append(item)
    return filtered


def live_usefulness_score(item: MemoryItem, *, reference_time_ms: int | None = None) -> float:
    freshness = 0.0
    if item.last_seen_at_ms is not None:
        reference_ms = reference_time_ms or now_ms()
        age_ms = max(0, reference_ms - item.last_seen_at_ms)
        freshness = max(0.0, 1.0 - min(age_ms / (7 * 24 * 60 * 60 * 1000), 1.0))
    status_bonus = 0.15 if item.status == "active" else 0.0
    return (
        (item.relevance * 0.45)
        + (item.confidence * 0.3)
        + (item.maturity * 0.15)
        + freshness * 0.1
        + status_bonus
    )


def sort_memory_items_for_live_use(items: Iterable[MemoryItem]) -> list[MemoryItem]:
    reference_ms = now_ms()
    return sorted(
        items,
        key=lambda item: (
            live_usefulness_score(item, reference_time_ms=reference_ms),
            item.last_seen_at_ms or 0,
            item.item_id,
        ),
        reverse=True,
    )


def build_retrieval_index_state(items: Iterable[MemoryItem]) -> RetrievalIndexState:
    reference_ms = now_ms()
    entries = tuple(
        RetrievalIndexEntry(
            item_id=item.item_id,
            score=live_usefulness_score(item, reference_time_ms=reference_ms),
            reasons=tuple(
                reason
                for reason in (
                    "high_relevance" if item.relevance >= 0.6 else "",
                    "high_confidence" if item.confidence >= 0.6 else "",
                    "mature" if item.maturity >= 0.5 else "",
                    "recent" if (item.last_seen_at_ms or 0) >= reference_ms - (3 * 24 * 60 * 60 * 1000) else "",
                    "active" if item.status == "active" else "",
                )
                if reason
            ),
            tags=item.tags,
            updated_at_ms=reference_ms,
        )
        for item in sort_memory_items_for_live_use(items)
    )
    return normalize_retrieval_index_state(
        RetrievalIndexState(
            updated_at_ms=reference_ms,
            entries=entries,
            metadata={"entry_count": len(entries)},
        )
    )


def tokenize_retrieval_text(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in text.split():
        token = normalize_semantic_key(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)
