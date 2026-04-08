from __future__ import annotations

from dataclasses import dataclass, replace

from backend.memory.normalization_v2 import build_stable_hash
from backend.memory.types_v2 import MemoryItem


def conflict_group_key(*, memory_class: str, scope: str, subject_key: str) -> str:
    return build_stable_hash("memory_conflict_group", memory_class, scope, subject_key)


def _dedupe_strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _metadata_string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [entry for entry in value if isinstance(entry, str) and entry]
    return []


def _merge_history(item: MemoryItem) -> list[dict[str, object]]:
    history = item.metadata.get("merge_history")
    if isinstance(history, list):
        return [dict(entry) for entry in history if isinstance(entry, dict)]
    return []


@dataclass(frozen=True, slots=True)
class MemoryConflictEntry:
    item: MemoryItem
    evidence_count: int

    @property
    def item_id(self) -> str:
        return self.item.item_id

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item.item_id,
            "memory_class": self.item.memory_class,
            "scope": self.item.scope,
            "session_id": self.item.session_id,
            "status": self.item.status,
            "summary": self.item.summary,
            "subject_key": self.item.subject_key,
            "value_key": self.item.value_key,
            "confidence": self.item.confidence,
            "relevance": self.item.relevance,
            "maturity": self.item.maturity,
            "tags": list(self.item.tags),
            "evidence_count": self.evidence_count,
            "last_seen_at_ms": self.item.last_seen_at_ms,
            "metadata": dict(self.item.metadata),
        }


@dataclass(frozen=True, slots=True)
class MemoryConflictGroup:
    group_key: str
    memory_class: str
    scope: str
    subject_key: str
    entries: tuple[MemoryConflictEntry, ...]

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(entry.item_id for entry in self.entries)

    def to_dict(self) -> dict[str, object]:
        return {
            "group_key": self.group_key,
            "memory_class": self.memory_class,
            "scope": self.scope,
            "subject_key": self.subject_key,
            "count": len(self.entries),
            "item_ids": list(self.item_ids),
            "items": [entry.to_dict() for entry in self.entries],
        }


class MemoryConflictServiceV2:
    def build_groups(self, items: list[MemoryItem]) -> list[MemoryConflictGroup]:
        grouped: dict[tuple[str, str, str], list[MemoryItem]] = {}
        for item in items:
            if item.status not in {"active", "conflicted"}:
                continue
            if not item.subject_key or not item.value_key:
                continue
            key = (item.memory_class, item.scope, item.subject_key)
            grouped.setdefault(key, []).append(item)

        groups: list[MemoryConflictGroup] = []
        for (memory_class, scope, subject_key), group_items in grouped.items():
            value_keys = {item.value_key for item in group_items if item.value_key}
            if len(group_items) < 2 or len(value_keys) < 2:
                continue
            entries = tuple(
                sorted(
                    (
                        MemoryConflictEntry(item=item, evidence_count=len(item.evidence_ids))
                        for item in group_items
                    ),
                    key=lambda entry: (
                        entry.item.status != "active",
                        -(entry.item.last_seen_at_ms or 0),
                        entry.item.item_id,
                    ),
                )
            )
            groups.append(
                MemoryConflictGroup(
                    group_key=conflict_group_key(
                        memory_class=memory_class,
                        scope=scope,
                        subject_key=subject_key,
                    ),
                    memory_class=memory_class,
                    scope=scope,
                    subject_key=subject_key,
                    entries=entries,
                )
            )
        groups.sort(key=lambda group: (group.memory_class, group.scope, group.subject_key))
        return groups

    def merge_items(
        self,
        *,
        target_item: MemoryItem,
        source_item: MemoryItem,
        actor: str,
        reason: str,
        merged_at_ms: int,
        suppress_source: bool = True,
    ) -> tuple[MemoryItem, MemoryItem, dict[str, object]]:
        self._validate_merge_pair(target_item=target_item, source_item=source_item)
        normalized_actor = actor.strip() or "operator"
        normalized_reason = reason.strip() or "manual_merge"
        merge_event = {
            "event": "merge_items",
            "actor": normalized_actor,
            "reason": normalized_reason,
            "merged_at_ms": merged_at_ms,
            "target_item_id": target_item.item_id,
            "source_item_id": source_item.item_id,
            "group_key": conflict_group_key(
                memory_class=target_item.memory_class,
                scope=target_item.scope,
                subject_key=target_item.subject_key,
            ),
        }

        merged_target = replace(
            target_item,
            status="active",
            session_id=target_item.session_id or source_item.session_id,
            summary=target_item.summary or source_item.summary,
            structured_value={**source_item.structured_value, **target_item.structured_value},
            confidence=max(target_item.confidence, source_item.confidence),
            relevance=max(target_item.relevance, source_item.relevance),
            maturity=max(target_item.maturity, source_item.maturity),
            first_seen_at_ms=self._min_ts(
                target_item.first_seen_at_ms,
                source_item.first_seen_at_ms,
            ),
            last_seen_at_ms=self._max_ts(
                target_item.last_seen_at_ms,
                source_item.last_seen_at_ms,
                merged_at_ms,
            ),
            last_promoted_at_ms=self._max_ts(
                target_item.last_promoted_at_ms,
                source_item.last_promoted_at_ms,
                merged_at_ms,
            ),
            source_kinds=_dedupe_strings((*target_item.source_kinds, *source_item.source_kinds)),
            evidence_ids=_dedupe_strings((*target_item.evidence_ids, *source_item.evidence_ids)),
            relation_ids=_dedupe_strings((*target_item.relation_ids, *source_item.relation_ids)),
            tags=_dedupe_strings((*target_item.tags, *source_item.tags)),
            correction_notes=_dedupe_strings(
                (
                    *target_item.correction_notes,
                    *source_item.correction_notes,
                    f"merged_source_item={source_item.item_id}",
                )
            ),
            metadata={
                **source_item.metadata,
                **target_item.metadata,
                "merged_from_item_ids": list(
                    dict.fromkeys(
                        [
                            * _metadata_string_list(target_item.metadata.get("merged_from_item_ids")),
                            source_item.item_id,
                            * _metadata_string_list(source_item.metadata.get("merged_from_item_ids")),
                        ]
                    )
                ),
                "merge_history": [
                    *_merge_history(target_item),
                    *_merge_history(source_item),
                    merge_event,
                ],
                "audit_events": [
                    *[
                        entry
                        for entry in list(target_item.metadata.get("audit_events") or [])
                        if isinstance(entry, dict)
                    ][-40:],
                    merge_event,
                ][-50:],
                "conflict_resolution": {
                    "action": "merged",
                    "actor": normalized_actor,
                    "reason": normalized_reason,
                    "resolved_at_ms": merged_at_ms,
                    "source_item_id": source_item.item_id,
                },
            },
        )

        source_status = "suppressed" if suppress_source else source_item.status
        merged_source = replace(
            source_item,
            status=source_status,
            last_seen_at_ms=self._max_ts(source_item.last_seen_at_ms, merged_at_ms),
            correction_notes=_dedupe_strings(
                (*source_item.correction_notes, f"merged_into_item={target_item.item_id}")
            ),
            metadata={
                **source_item.metadata,
                "merge_history": [*_merge_history(source_item), merge_event],
                "audit_events": [
                    *[
                        entry
                        for entry in list(source_item.metadata.get("audit_events") or [])
                        if isinstance(entry, dict)
                    ][-40:],
                    merge_event,
                ][-50:],
                "merged_into_item_id": target_item.item_id,
                "conflict_resolution": {
                    "action": "suppressed_after_merge" if suppress_source else "merged_copy",
                    "actor": normalized_actor,
                    "reason": normalized_reason,
                    "resolved_at_ms": merged_at_ms,
                    "target_item_id": target_item.item_id,
                },
            },
        )
        return merged_target, merged_source, merge_event

    def build_item_audit_trail(self, *, item: MemoryItem) -> dict[str, object]:
        return {
            "item_id": item.item_id,
            "status": item.status,
            "merge_history": _merge_history(item),
            "conflict_resolution": item.metadata.get("conflict_resolution"),
            "merged_from_item_ids": _metadata_string_list(item.metadata.get("merged_from_item_ids")),
            "merged_into_item_id": item.metadata.get("merged_into_item_id"),
            "maintenance_origin": item.metadata.get("origin"),
            "audit_events": [
                dict(entry)
                for entry in list(item.metadata.get("audit_events") or [])
                if isinstance(entry, dict)
            ],
            "correction_notes": list(item.correction_notes),
        }

    @staticmethod
    def _validate_merge_pair(*, target_item: MemoryItem, source_item: MemoryItem) -> None:
        if target_item.item_id == source_item.item_id:
            raise ValueError("Cannot merge an item into itself.")
        same_group = (
            target_item.memory_class == source_item.memory_class
            and target_item.scope == source_item.scope
            and target_item.subject_key == source_item.subject_key
            and bool(target_item.subject_key)
        )
        if not same_group:
            raise ValueError("Merge requires items from the same conflict group.")
        if target_item.value_key == source_item.value_key:
            raise ValueError("Merge requires conflicting values, not duplicate values.")

    @staticmethod
    def _min_ts(*values: int | None) -> int | None:
        valid = [value for value in values if value is not None]
        return min(valid) if valid else None

    @staticmethod
    def _max_ts(*values: int | None) -> int | None:
        valid = [value for value in values if value is not None]
        return max(valid) if valid else None


__all__ = [
    "MemoryConflictEntry",
    "MemoryConflictGroup",
    "MemoryConflictServiceV2",
    "conflict_group_key",
]
