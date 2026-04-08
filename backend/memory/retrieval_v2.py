from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.core.storage import now_ms
from backend.memory.indexing_v2 import live_usefulness_score, sort_memory_items_for_live_use, tokenize_retrieval_text
from backend.memory.retrieval_policy_v2 import RetrievalPolicyV2, build_default_retrieval_policy
from backend.memory.repository_v2 import MemoryRepositoryV2
from backend.memory.types_v2 import MaintenanceState, MemoryEvidence, MemoryItem, RetrievalIndexState


def _truncate(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


@dataclass(frozen=True, slots=True)
class LiveMemoryBundleRequest:
    session_id: str | None
    query_text: str | None = None
    intention_text: str | None = None
    memory_classes: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    limit: int | None = None
    evidence_limit_per_item: int | None = None


@dataclass(frozen=True, slots=True)
class LiveMemoryBundleEntry:
    item: MemoryItem
    score: float
    ranking: dict[str, object]
    evidence: tuple[MemoryEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "item": {
                "item_id": self.item.item_id,
                "memory_class": self.item.memory_class,
                "scope": self.item.scope,
                "session_id": self.item.session_id,
                "status": self.item.status,
                "summary": self.item.summary,
                "structured_value": dict(self.item.structured_value),
                "confidence": self.item.confidence,
                "relevance": self.item.relevance,
                "maturity": self.item.maturity,
                "tags": list(self.item.tags),
                "last_seen_at_ms": self.item.last_seen_at_ms,
                "last_promoted_at_ms": self.item.last_promoted_at_ms,
            },
            "score": self.score,
            "ranking": dict(self.ranking),
            "evidence_summary": {
                "count": len(self.evidence),
                "latest_captured_at_ms": max((record.captured_at_ms for record in self.evidence), default=None),
                "records": [
                    {
                        "evidence_id": record.evidence_id,
                        "evidence_kind": record.evidence_kind,
                        "excerpt": _truncate(record.excerpt, max_chars=180),
                        "confidence": record.confidence,
                        "captured_at_ms": record.captured_at_ms,
                        "source_ref": record.source_ref,
                    }
                    for record in self.evidence
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class LiveMemoryBundle:
    session_id: str | None
    query_text: str | None
    intention_text: str | None
    generated_at_ms: int
    entries: tuple[LiveMemoryBundleEntry, ...]
    retrieval_index_state: RetrievalIndexState
    maintenance_state: MaintenanceState

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "query_text": self.query_text,
            "intention_text": self.intention_text,
            "generated_at_ms": self.generated_at_ms,
            "count": len(self.entries),
            "items": [entry.to_dict() for entry in self.entries],
            "retrieval_index": {
                "updated_at_ms": self.retrieval_index_state.updated_at_ms,
                "entry_count": len(self.retrieval_index_state.entries),
                "metadata": dict(self.retrieval_index_state.metadata),
            },
            "maintenance_state": {
                "updated_at_ms": self.maintenance_state.updated_at_ms,
                "last_candidate_consolidation_at_ms": self.maintenance_state.last_candidate_consolidation_at_ms,
                "last_observation_promotion_at_ms": self.maintenance_state.last_observation_promotion_at_ms,
                "last_dedup_at_ms": self.maintenance_state.last_dedup_at_ms,
                "metadata": dict(self.maintenance_state.metadata),
            },
        }


class MemoryRetrievalServiceV2:
    def __init__(
        self,
        *,
        repository: MemoryRepositoryV2,
        policy: RetrievalPolicyV2 | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy or build_default_retrieval_policy()

    def build_live_bundle(self, *, request: LiveMemoryBundleRequest) -> LiveMemoryBundle:
        limit = self.policy.clamp_limit(request.limit)
        evidence_limit = self.policy.clamp_evidence_limit(request.evidence_limit_per_item)
        generated_at_ms = now_ms()
        merged_query = self._merge_query_text(request=request)
        query_tokens = self.policy.normalize_query_tokens(merged_query)
        query_tags = self.policy.normalize_query_tags(merged_query)
        class_hints = self._extract_class_hints(query_tokens=query_tokens, explicit=request.memory_classes)
        status_filter = self._normalize_status_filter(explicit=request.statuses)

        retrieval_state = self.repository.read_retrieval_index_state()
        maintenance_state = self.repository.read_maintenance_state()
        indexed_scores = {
            entry.item_id: {
                "index_score": entry.score,
                "index_reasons": list(entry.reasons),
            }
            for entry in retrieval_state.entries
        }

        eligible_items = [
            item
            for item in self.repository.list_items()
            if item.status not in {"suppressed", "deleted", "archived"}
            and (not status_filter or item.status in status_filter)
            and (not class_hints or item.memory_class in class_hints)
        ]
        sorted_items = sort_memory_items_for_live_use(eligible_items)

        scored_entries: list[tuple[float, MemoryItem, dict[str, object]]] = []
        for item in sorted_items:
            base_score = float(indexed_scores.get(item.item_id, {}).get("index_score", 0.0))
            fallback_score = live_usefulness_score(item, reference_time_ms=generated_at_ms)
            effective_base = base_score if base_score > 0.0 else fallback_score
            session_affinity_bonus = (
                self.policy.session_affinity_bonus
                if request.session_id and item.session_id == request.session_id
                else 0.0
            )
            recency_bonus = self._compute_recency_bonus(item=item, reference_time_ms=generated_at_ms)
            query_bonus, query_details = self._compute_query_bonus(
                item=item,
                query_tokens=query_tokens,
                query_tags=query_tags,
                class_hints=class_hints,
            )
            conflict_penalty = self.policy.conflict_penalty if item.status == "conflicted" else 0.0
            final_score = effective_base + session_affinity_bonus + recency_bonus + query_bonus - conflict_penalty
            inclusion_reasons = self._build_inclusion_reasons(
                item=item,
                request_session_id=request.session_id,
                query_details=query_details,
                recency_bonus=recency_bonus,
                index_reasons=indexed_scores.get(item.item_id, {}).get("index_reasons", []),
            )
            scored_entries.append(
                (
                    final_score,
                    item,
                    {
                        "final_score": round(final_score, 6),
                        "index_score": round(base_score, 6),
                        "fallback_score": round(fallback_score, 6),
                        "session_affinity_bonus": round(session_affinity_bonus, 6),
                        "recency_bonus": round(recency_bonus, 6),
                        "query_bonus": round(query_bonus, 6),
                        "conflict_penalty": round(conflict_penalty, 6),
                        "index_reasons": list(indexed_scores.get(item.item_id, {}).get("index_reasons", [])),
                        "inclusion_reasons": inclusion_reasons,
                        "query": {
                            "tokens": list(query_tokens),
                            "tags": list(query_tags),
                            "class_hints": sorted(class_hints),
                            "matched_summary_tokens": sorted(query_details.get("matched_summary_tokens", ())),
                            "matched_tags": sorted(query_details.get("matched_tags", ())),
                            "class_match": bool(query_details.get("class_match")),
                        },
                        "flags": {
                            "is_conflicted": item.status == "conflicted",
                            "has_query_match": bool(query_details.get("has_query_match")),
                        },
                        "confidence": item.confidence,
                        "relevance": item.relevance,
                        "maturity": item.maturity,
                        "last_seen_at_ms": item.last_seen_at_ms,
                        "status": item.status,
                    },
                )
            )

        scored_entries.sort(key=lambda row: (row[0], row[1].last_seen_at_ms or 0, row[1].item_id), reverse=True)
        selected = scored_entries[:limit] if limit else []
        entries: list[LiveMemoryBundleEntry] = []
        for score, item, ranking in selected:
            evidence = self.repository.list_item_evidence(item_id=item.item_id)
            if evidence_limit:
                evidence = evidence[:evidence_limit]
            entries.append(
                LiveMemoryBundleEntry(
                    item=item,
                    score=score,
                    ranking=ranking,
                    evidence=tuple(evidence),
                )
            )

        return LiveMemoryBundle(
            session_id=request.session_id,
            query_text=request.query_text,
            intention_text=request.intention_text,
            generated_at_ms=generated_at_ms,
            entries=tuple(entries),
            retrieval_index_state=retrieval_state,
            maintenance_state=maintenance_state,
        )

    def _compute_recency_bonus(self, *, item: MemoryItem, reference_time_ms: int) -> float:
        if item.last_seen_at_ms is None:
            return 0.0
        age_ms = max(0, reference_time_ms - item.last_seen_at_ms)
        one_day_ms = 24 * 60 * 60 * 1000
        if age_ms <= one_day_ms:
            return self.policy.recency_bonus_fresh
        if age_ms <= (3 * one_day_ms):
            return self.policy.recency_bonus_recent
        return 0.0

    @staticmethod
    def _merge_query_text(*, request: LiveMemoryBundleRequest) -> str | None:
        parts: list[str] = []
        if request.query_text and request.query_text.strip():
            parts.append(request.query_text.strip())
        if request.intention_text and request.intention_text.strip():
            parts.append(request.intention_text.strip())
        if not parts:
            return None
        return " ".join(parts)

    def _normalize_status_filter(self, *, explicit: tuple[str, ...]) -> set[str]:
        normalized = {status.strip().lower() for status in explicit if status and status.strip()}
        return normalized

    @staticmethod
    def _extract_class_hints(*, query_tokens: tuple[str, ...], explicit: tuple[str, ...]) -> set[str]:
        known_classes = {
            "identity",
            "preference",
            "routine",
            "ongoing_thread",
            "social",
            "location",
            "important_object",
            "habit",
            "recent_fact",
        }
        hints = {value.strip().lower() for value in explicit if value and value.strip()}
        token_set = set(query_tokens)
        class_aliases = {
            "ongoing_thread": {"thread", "project", "workstream", "task"},
            "important_object": {"object", "thing", "device", "item"},
            "recent_fact": {"fact", "recent"},
            "preference": {"preference", "prefer", "likes"},
            "location": {"location", "place", "where"},
            "routine": {"routine", "habitual"},
            "identity": {"identity", "profile"},
            "social": {"social", "relationship", "people"},
            "habit": {"habit", "pattern"},
        }
        for memory_class in known_classes:
            if memory_class in token_set:
                hints.add(memory_class)
                continue
            if token_set.intersection(class_aliases.get(memory_class, set())):
                hints.add(memory_class)
        return {value for value in hints if value in known_classes}

    def _compute_query_bonus(
        self,
        *,
        item: MemoryItem,
        query_tokens: tuple[str, ...],
        query_tags: tuple[str, ...],
        class_hints: set[str],
    ) -> tuple[float, dict[str, object]]:
        if not query_tokens and not query_tags and not class_hints:
            return 0.0, {"has_query_match": False}

        summary_tokens = set(tokenize_retrieval_text(item.summary))
        query_token_set = set(query_tokens)
        matched_summary_tokens = query_token_set.intersection(summary_tokens)

        query_tag_set = set(query_tags)
        item_tag_set = set(item.tags)
        matched_tags = query_tag_set.intersection(item_tag_set)

        class_match = bool(class_hints and item.memory_class in class_hints)

        query_overlap_ratio = (
            len(matched_summary_tokens) / float(len(query_token_set))
            if query_token_set
            else 0.0
        )
        tag_overlap_ratio = (
            len(matched_tags) / float(len(query_tag_set))
            if query_tag_set
            else 0.0
        )

        summary_bonus = self.policy.query_match_bonus * query_overlap_ratio
        tag_bonus = self.policy.tag_match_bonus * tag_overlap_ratio
        class_bonus = self.policy.class_match_bonus if class_match else 0.0
        total_bonus = summary_bonus + tag_bonus + class_bonus

        return total_bonus, {
            "has_query_match": total_bonus > 0.0,
            "matched_summary_tokens": tuple(matched_summary_tokens),
            "matched_tags": tuple(matched_tags),
            "class_match": class_match,
            "query_overlap_ratio": round(query_overlap_ratio, 6),
            "tag_overlap_ratio": round(tag_overlap_ratio, 6),
            "summary_bonus": round(summary_bonus, 6),
            "tag_bonus": round(tag_bonus, 6),
            "class_bonus": round(class_bonus, 6),
        }

    @staticmethod
    def _build_inclusion_reasons(
        *,
        item: MemoryItem,
        request_session_id: str | None,
        query_details: dict[str, object],
        recency_bonus: float,
        index_reasons: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        reasons.extend(reason for reason in index_reasons if reason)
        if request_session_id and item.session_id == request_session_id:
            reasons.append("session_affinity")
        if query_details.get("class_match"):
            reasons.append("query_class_match")
        if query_details.get("matched_tags"):
            reasons.append("query_tag_overlap")
        if query_details.get("matched_summary_tokens"):
            reasons.append("query_summary_overlap")
        if recency_bonus > 0.0:
            reasons.append("recently_seen")
        if item.status == "conflicted":
            reasons.append("conflict_visible_penalized")
        # Preserve order while removing duplicates.
        return list(dict.fromkeys(reasons))


def summarize_recent_maintenance(maintenance_state: MaintenanceState) -> dict[str, Any]:
    metadata = dict(maintenance_state.metadata)
    raw_results = metadata.get("last_results")
    if not isinstance(raw_results, list):
        raw_results = []
    recent_results: list[dict[str, object]] = []
    for raw in raw_results[:10]:
        if isinstance(raw, dict):
            recent_results.append(dict(raw))
    return {
        "updated_at_ms": maintenance_state.updated_at_ms,
        "last_candidate_consolidation_at_ms": maintenance_state.last_candidate_consolidation_at_ms,
        "last_observation_promotion_at_ms": maintenance_state.last_observation_promotion_at_ms,
        "last_dedup_at_ms": maintenance_state.last_dedup_at_ms,
        "last_scope": metadata.get("last_scope"),
        "last_phase": metadata.get("last_phase"),
        "last_dry_run": metadata.get("last_dry_run"),
        "last_session_ids": metadata.get("last_session_ids"),
        "last_results": recent_results,
    }


__all__ = [
    "LiveMemoryBundle",
    "LiveMemoryBundleEntry",
    "LiveMemoryBundleRequest",
    "MemoryRetrievalServiceV2",
    "summarize_recent_maintenance",
]
