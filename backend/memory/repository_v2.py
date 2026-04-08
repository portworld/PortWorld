from __future__ import annotations

from dataclasses import replace
from typing import Any

from backend.core.storage import BackendStorage, now_ms
from backend.memory.conflicts_v2 import (
    MemoryConflictServiceV2,
    MemoryConflictGroup,
    conflict_group_key,
)
from backend.memory.indexing_v2 import build_retrieval_index_state, filter_memory_items
from backend.memory.normalization_v2 import (
    normalize_memory_candidate,
    normalize_memory_evidence,
    normalize_memory_item,
    normalize_semantic_key,
    normalize_session_observation,
)
from backend.memory.types_v2 import (
    MaintenanceState,
    MemoryCandidateV2,
    MemoryEvidence,
    MemoryItem,
    RetrievalIndexState,
    SessionObservation,
)


class MemoryRepositoryV2:
    def __init__(self, *, storage: BackendStorage) -> None:
        self.storage = storage
        self.conflicts = MemoryConflictServiceV2()

    def upsert_item(self, *, item: MemoryItem) -> MemoryItem:
        normalized = normalize_memory_item(item)
        return self.storage.write_memory_item(item=normalized)

    def get_item(self, *, item_id: str) -> MemoryItem | None:
        return self.storage.read_memory_item(item_id=item_id)

    def list_items(
        self,
        *,
        scope: str | None = None,
        memory_class: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        items = filter_memory_items(
            self.storage.list_memory_items(),
            scope=scope,
            memory_class=memory_class,
            status=status,
            tag=tag,
            session_id=session_id,
        )
        if limit is not None and limit >= 0:
            return items[:limit]
        return items

    def find_item_by_fingerprint(self, *, fingerprint: str) -> MemoryItem | None:
        normalized_fingerprint = fingerprint.strip()
        if not normalized_fingerprint:
            return None
        for item in self.storage.list_memory_items():
            if item.fingerprint == normalized_fingerprint:
                return item
        return None

    def find_conflicting_item(
        self,
        *,
        memory_class: str,
        scope: str,
        subject_key: str,
        value_key: str,
        allowed_statuses: tuple[str, ...] = ("active", "conflicted"),
    ) -> MemoryItem | None:
        for item in self.storage.list_memory_items():
            if item.memory_class != memory_class:
                continue
            if item.scope != scope:
                continue
            if item.subject_key != subject_key:
                continue
            if item.status not in allowed_statuses:
                continue
            if item.value_key == value_key:
                continue
            return item
        return None

    def suppress_item(
        self,
        *,
        item_id: str,
        note: str | None = None,
        updated_at_ms: int | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> MemoryItem | None:
        item = self.get_item(item_id=item_id)
        if item is None:
            return None
        notes = list(item.correction_notes)
        if note:
            notes.append(note.strip())
        event_at_ms = updated_at_ms if updated_at_ms is not None else now_ms()
        metadata = dict(item.metadata)
        suppress_event = {
            "event": "suppress_item",
            "item_id": item_id,
            "actor": (actor or "system").strip() or "system",
            "reason": (reason or note or "manual_suppress").strip() or "manual_suppress",
            "updated_at_ms": event_at_ms,
        }
        metadata["last_suppress_event"] = suppress_event
        audit_events = list(metadata.get("audit_events") or [])
        audit_events.append(suppress_event)
        metadata["audit_events"] = audit_events[-50:]
        updated = replace(
            item,
            status="suppressed",
            correction_notes=tuple(note for note in notes if note),
            last_seen_at_ms=max(item.last_seen_at_ms or 0, event_at_ms),
            metadata=metadata,
        )
        return self.upsert_item(item=updated)

    def correct_item(
        self,
        *,
        item_id: str,
        summary: str | None = None,
        structured_value: dict[str, Any] | None = None,
        confidence: float | None = None,
        relevance: float | None = None,
        maturity: float | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        correction_note: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> MemoryItem | None:
        item = self.get_item(item_id=item_id)
        if item is None:
            return None
        notes = list(item.correction_notes)
        if correction_note:
            notes.append(correction_note.strip())
        updated = replace(
            item,
            session_id=session_id if session_id is not None else item.session_id,
            summary=summary if summary is not None else item.summary,
            structured_value=structured_value if structured_value is not None else item.structured_value,
            confidence=confidence if confidence is not None else item.confidence,
            relevance=relevance if relevance is not None else item.relevance,
            maturity=maturity if maturity is not None else item.maturity,
            tags=tuple(tags) if tags is not None else item.tags,
            correction_notes=tuple(note for note in notes if note),
            status=status if status is not None else item.status,
            last_seen_at_ms=now_ms(),
        )
        return self.upsert_item(item=updated)

    def delete_item(self, *, item_id: str) -> bool:
        return self.storage.delete_memory_item(item_id=item_id)

    def attach_evidence(
        self,
        *,
        item_id: str,
        evidence: MemoryEvidence,
    ) -> MemoryEvidence:
        item = self.get_item(item_id=item_id)
        if item is None:
            raise KeyError(f"Memory item not found: {item_id!r}")
        stored_evidence = self.storage.write_memory_evidence(
            evidence=normalize_memory_evidence(replace(evidence, item_id=item_id))
        )
        evidence_ids = tuple(dict.fromkeys([*item.evidence_ids, stored_evidence.evidence_id]))
        source_kinds = tuple(dict.fromkeys([*item.source_kinds, stored_evidence.evidence_kind]))
        updated_item = replace(
            item,
            evidence_ids=evidence_ids,
            source_kinds=source_kinds,
            last_seen_at_ms=max(item.last_seen_at_ms or 0, stored_evidence.captured_at_ms),
        )
        self.upsert_item(item=updated_item)
        return stored_evidence

    def list_item_evidence(self, *, item_id: str) -> list[MemoryEvidence]:
        item = self.get_item(item_id=item_id)
        if item is None:
            return []
        evidence: list[MemoryEvidence] = []
        for evidence_id in item.evidence_ids:
            record = self.storage.read_memory_evidence(evidence_id=evidence_id)
            if record is not None:
                evidence.append(record)
        evidence.sort(key=lambda record: (record.captured_at_ms, record.evidence_id), reverse=True)
        return evidence

    def create_candidate(
        self,
        *,
        session_id: str,
        candidate: MemoryCandidateV2,
    ) -> MemoryCandidateV2:
        normalized = normalize_memory_candidate(candidate)
        return self.storage.write_memory_candidate_v2(session_id=session_id, candidate=normalized)

    def list_candidates(self, *, session_id: str) -> list[MemoryCandidateV2]:
        return self.storage.read_memory_candidates_v2(session_id=session_id)

    def get_candidate(
        self,
        *,
        session_id: str,
        candidate_id: str,
    ) -> MemoryCandidateV2 | None:
        for candidate in self.list_candidates(session_id=session_id):
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def create_observation(
        self,
        *,
        session_id: str,
        observation: SessionObservation,
    ) -> SessionObservation:
        normalized = normalize_session_observation(observation)
        return self.storage.write_session_observation(session_id=session_id, observation=normalized)

    def list_observations(self, *, session_id: str) -> list[SessionObservation]:
        return self.storage.read_session_observations(session_id=session_id)

    def get_observation(
        self,
        *,
        session_id: str,
        observation_id: str,
    ) -> SessionObservation | None:
        for observation in self.list_observations(session_id=session_id):
            if observation.observation_id == observation_id:
                return observation
        return None

    def read_evidence(self, *, evidence_id: str) -> MemoryEvidence | None:
        return self.storage.read_memory_evidence(evidence_id=evidence_id)

    def list_evidence_records(
        self,
        *,
        evidence_ids: list[str] | tuple[str, ...],
    ) -> list[MemoryEvidence]:
        evidence: list[MemoryEvidence] = []
        seen: set[str] = set()
        for evidence_id in evidence_ids:
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            record = self.read_evidence(evidence_id=evidence_id)
            if record is not None:
                evidence.append(record)
        evidence.sort(key=lambda record: (record.captured_at_ms, record.evidence_id), reverse=True)
        return evidence

    def list_session_ids_with_memory_activity(self) -> list[str]:
        session_ids = {
            eligibility.session_id
            for eligibility in self.storage.list_session_memory_retention_eligibility(
                retention_days=365000,
            )
        }
        for item in self.storage.list_memory_items():
            if item.session_id:
                session_ids.add(item.session_id)
        return sorted(session_ids)

    def list_conflict_groups(self) -> list[MemoryConflictGroup]:
        return list(self.conflicts.build_groups(self.storage.list_memory_items()))

    def get_conflict_group(self, *, group_key: str) -> MemoryConflictGroup | None:
        normalized_key = group_key.strip()
        if not normalized_key:
            return None
        for group in self.list_conflict_groups():
            if group.group_key == normalized_key:
                return group
        return None

    def list_conflicting_items(self) -> list[MemoryItem]:
        item_ids: list[str] = []
        for group in self.list_conflict_groups():
            item_ids.extend(group.item_ids)
        seen: set[str] = set()
        items: list[MemoryItem] = []
        for item_id in item_ids:
            if item_id in seen:
                continue
            seen.add(item_id)
            item = self.get_item(item_id=item_id)
            if item is not None:
                items.append(item)
        items.sort(key=lambda item: (item.last_seen_at_ms or 0, item.item_id), reverse=True)
        return items

    def inspect_conflict_for_item(self, *, item_id: str) -> MemoryConflictGroup | None:
        item = self.get_item(item_id=item_id)
        if item is None:
            return None
        if not item.subject_key:
            return None
        return self.get_conflict_group(
            group_key=conflict_group_key(
                memory_class=item.memory_class,
                scope=item.scope,
                subject_key=item.subject_key,
            )
        )

    def inspect_candidate_conflict(
        self,
        *,
        session_id: str,
        candidate_id: str,
    ) -> dict[str, object] | None:
        candidate = self.get_candidate(session_id=session_id, candidate_id=candidate_id)
        if candidate is None:
            return None
        subject_key = normalize_semantic_key(candidate.metadata.get("subject_key") or candidate.summary)
        value_key = normalize_semantic_key(candidate.metadata.get("value_key") or candidate.fact)
        if not subject_key or not value_key:
            return None
        conflicting_item = self.find_conflicting_item(
            memory_class=candidate.memory_class,
            scope=candidate.scope,
            subject_key=subject_key,
            value_key=value_key,
        )
        if conflicting_item is None:
            return None
        group = self.get_conflict_group(
            group_key=conflict_group_key(
                memory_class=candidate.memory_class,
                scope=candidate.scope,
                subject_key=subject_key,
            )
        )
        return {
            "candidate_id": candidate.candidate_id,
            "session_id": candidate.session_id,
            "subject_key": subject_key,
            "value_key": value_key,
            "conflicting_item_id": conflicting_item.item_id,
            "conflict_group": group.to_dict() if group is not None else None,
        }

    def merge_items(
        self,
        *,
        target_item_id: str,
        source_item_id: str,
        actor: str,
        reason: str,
        merged_at_ms: int | None = None,
        suppress_source: bool = True,
    ) -> dict[str, object]:
        target = self.get_item(item_id=target_item_id)
        if target is None:
            raise KeyError(f"Target memory item not found: {target_item_id!r}")
        source = self.get_item(item_id=source_item_id)
        if source is None:
            raise KeyError(f"Source memory item not found: {source_item_id!r}")
        event_at_ms = merged_at_ms if merged_at_ms is not None else now_ms()
        merged_target, merged_source, merge_event = self.conflicts.merge_items(
            target_item=target,
            source_item=source,
            actor=actor,
            reason=reason,
            merged_at_ms=event_at_ms,
            suppress_source=suppress_source,
        )
        stored_target = self.upsert_item(item=merged_target)
        stored_source = self.upsert_item(item=merged_source)
        merge_evidence = MemoryEvidence(
            evidence_id="",
            evidence_kind="maintenance_merge",
            session_id=stored_target.session_id or stored_source.session_id,
            source_ref=f"memory_merge:{stored_source.item_id}",
            excerpt=f"Merged memory item {stored_source.item_id} into {stored_target.item_id}",
            captured_at_ms=event_at_ms,
            confidence=1.0,
            item_id=stored_target.item_id,
            tags=stored_target.tags,
            metadata=merge_event,
        )
        self.attach_evidence(item_id=stored_target.item_id, evidence=merge_evidence)
        refreshed_target = self.get_item(item_id=stored_target.item_id) or stored_target
        return {
            "target_item": refreshed_target,
            "source_item": stored_source,
            "merge_event": merge_event,
        }

    def build_item_audit_trail(self, *, item_id: str) -> dict[str, object] | None:
        item = self.get_item(item_id=item_id)
        if item is None:
            return None
        return self.conflicts.build_item_audit_trail(item=item)

    def suppress_conflict_side(
        self,
        *,
        item_id: str,
        actor: str,
        reason: str,
        updated_at_ms: int | None = None,
    ) -> MemoryItem | None:
        return self.suppress_item(
            item_id=item_id,
            note=f"Conflict side suppressed: {reason.strip() or 'manual_conflict_suppress'}",
            updated_at_ms=updated_at_ms,
            actor=actor,
            reason=reason,
        )

    def read_retrieval_index_state(self) -> RetrievalIndexState:
        return self.storage.read_retrieval_index_state()

    def rebuild_retrieval_index_state(self) -> RetrievalIndexState:
        state = build_retrieval_index_state(self.storage.list_memory_items())
        return self.storage.write_retrieval_index_state(state=state)

    def write_retrieval_index_state(self, *, state: RetrievalIndexState) -> RetrievalIndexState:
        return self.storage.write_retrieval_index_state(state=state)

    def read_maintenance_state(self) -> MaintenanceState:
        return self.storage.read_maintenance_state()

    def write_maintenance_state(self, *, state: MaintenanceState) -> MaintenanceState:
        return self.storage.write_maintenance_state(state=state)
