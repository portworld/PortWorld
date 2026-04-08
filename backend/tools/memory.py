from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from json import JSONDecodeError
from typing import Any

from backend.core.storage import BackendStorage, RealtimeReadOnlyStorageView
from backend.memory.cross_session import parse_cross_session_markdown
from backend.memory.retrieval_v2 import LiveMemoryBundleRequest, MemoryRetrievalServiceV2
from backend.memory.repository_v2 import MemoryRepositoryV2
from backend.memory.types_v2 import MemoryEvidence, MemoryItem
from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.results import tool_error, tool_ok

logger = logging.getLogger(__name__)


class MemoryScope(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    CROSS_SESSION = "cross_session"


class MemoryV2ToolMode(str, Enum):
    LIST_ITEMS = "list_items"
    GET_ITEM = "get_item"
    GET_ITEM_EVIDENCE = "get_item_evidence"
    GET_LIVE_BUNDLE = "get_live_bundle"
    LIST_CONFLICTS = "list_conflicts"
    GET_CONFLICT_GROUP = "get_conflict_group"
    MERGE_ITEMS = "merge_items"
    SUPPRESS_CONFLICT_SIDE = "suppress_conflict_side"
    CORRECT_ITEM = "correct_item"
    SUPPRESS_ITEM = "suppress_item"
    DELETE_ITEM = "delete_item"


@dataclass(frozen=True, slots=True)
class MemoryToolExecutor:
    storage: RealtimeReadOnlyStorageView
    memory_scope: MemoryScope

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.memory_scope is MemoryScope.SHORT_TERM:
                structured = self.storage.read_short_term_memory(session_id=call.session_id)
                markdown = self.storage.read_short_term_memory_markdown(session_id=call.session_id)
            elif self.memory_scope is MemoryScope.LONG_TERM:
                structured = self.storage.read_session_memory(session_id=call.session_id)
                markdown = self.storage.read_session_memory_markdown(session_id=call.session_id)
            elif self.memory_scope is MemoryScope.CROSS_SESSION:
                markdown = self.storage.read_cross_session_memory()
                structured = parse_cross_session_markdown(markdown) if markdown else {}
            else:  # pragma: no cover - defensive for future enum changes
                raise ValueError(f"Unsupported memory scope: {self.memory_scope}")
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "Memory tool read failed session_id=%s call_id=%s scope=%s",
                call.session_id,
                call.call_id,
                self.memory_scope.value,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="MEMORY_READ_FAILED",
                error_message="Memory context unavailable",
                payload={
                    "scope": self.memory_scope.value,
                    "session_id": call.session_id,
                    "available": False,
                    "markdown": "",
                    "structured": {},
                },
            )

        available = bool(structured)
        return tool_ok(
            call=call,
            payload={
                "scope": self.memory_scope.value,
                "session_id": call.session_id,
                "available": available,
                "markdown": markdown if available else "",
                "structured": structured if available else {},
            },
        )


@dataclass(frozen=True, slots=True)
class MemoryV2ToolExecutor:
    storage: BackendStorage
    mode: MemoryV2ToolMode

    async def __call__(self, call: ToolCall) -> ToolResult:
        repository = MemoryRepositoryV2(storage=self.storage)
        try:
            if self.mode is MemoryV2ToolMode.LIST_ITEMS:
                payload = await asyncio.to_thread(self._list_items_payload, repository, call.arguments)
            elif self.mode is MemoryV2ToolMode.GET_ITEM:
                payload = await asyncio.to_thread(self._get_item_payload, repository, call.arguments)
            elif self.mode is MemoryV2ToolMode.GET_ITEM_EVIDENCE:
                payload = await asyncio.to_thread(
                    self._get_item_evidence_payload,
                    repository,
                    call.arguments,
                )
            elif self.mode is MemoryV2ToolMode.GET_LIVE_BUNDLE:
                payload = await asyncio.to_thread(
                    self._get_live_bundle_payload,
                    repository,
                    call.arguments,
                    call.session_id,
                )
            elif self.mode is MemoryV2ToolMode.LIST_CONFLICTS:
                payload = await asyncio.to_thread(
                    self._list_conflicts_payload,
                    repository,
                    call.arguments,
                )
            elif self.mode is MemoryV2ToolMode.GET_CONFLICT_GROUP:
                payload = await asyncio.to_thread(
                    self._get_conflict_group_payload,
                    repository,
                    call.arguments,
                )
            elif self.mode is MemoryV2ToolMode.MERGE_ITEMS:
                payload = await asyncio.to_thread(
                    self._merge_items_payload,
                    repository,
                    call.arguments,
                )
            elif self.mode is MemoryV2ToolMode.SUPPRESS_CONFLICT_SIDE:
                payload = await asyncio.to_thread(
                    self._suppress_conflict_side_payload,
                    repository,
                    call.arguments,
                )
            elif self.mode is MemoryV2ToolMode.CORRECT_ITEM:
                payload = await asyncio.to_thread(self._correct_item_payload, repository, call.arguments)
            elif self.mode is MemoryV2ToolMode.SUPPRESS_ITEM:
                payload = await asyncio.to_thread(self._suppress_item_payload, repository, call.arguments)
            elif self.mode is MemoryV2ToolMode.DELETE_ITEM:
                payload = await asyncio.to_thread(self._delete_item_payload, repository, call.arguments)
            else:  # pragma: no cover - defensive for future enum changes
                raise ValueError(f"Unsupported memory-v2 tool mode: {self.mode}")
        except KeyError as exc:
            return tool_error(
                call=call,
                error_code="MEMORY_ITEM_NOT_FOUND",
                error_message="Memory item not found",
                payload={"session_id": call.session_id, "detail": str(exc)},
            )
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "Memory-v2 tool failed session_id=%s call_id=%s mode=%s",
                call.session_id,
                call.call_id,
                self.mode.value,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="MEMORY_V2_TOOL_FAILED",
                error_message="Memory v2 operation failed",
                payload={"session_id": call.session_id},
            )

        return tool_ok(call=call, payload=payload)

    def _list_items_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        items = repository.list_items(
            scope=self._optional_str(arguments.get("scope")),
            memory_class=self._optional_str(arguments.get("memory_class")),
            status=self._optional_str(arguments.get("status")),
            tag=self._optional_str(arguments.get("tag")),
            session_id=self._optional_str(arguments.get("session_id")),
            limit=self._optional_int(arguments.get("limit")),
        )
        return {
            "count": len(items),
            "items": [self._serialize_item(item) for item in items],
        }

    def _get_item_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        item = repository.get_item(item_id=item_id)
        if item is None:
            return {"found": False, "item_id": item_id}
        return {
            "found": True,
            "item": self._serialize_item(item),
        }

    def _get_item_evidence_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        evidence = repository.list_item_evidence(item_id=item_id)
        return {
            "item_id": item_id,
            "count": len(evidence),
            "evidence": [self._serialize_evidence(record) for record in evidence],
        }

    def _correct_item_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        updated = repository.correct_item(
            item_id=item_id,
            summary=self._optional_str(arguments.get("summary")),
            structured_value=(
                arguments.get("structured_value")
                if isinstance(arguments.get("structured_value"), dict)
                else None
            ),
            confidence=self._optional_float(arguments.get("confidence")),
            relevance=self._optional_float(arguments.get("relevance")),
            maturity=self._optional_float(arguments.get("maturity")),
            tags=self._optional_str_list(arguments.get("tags")),
            correction_note=self._optional_str(arguments.get("correction_note")),
            session_id=self._optional_str(arguments.get("session_id")),
            status=self._optional_str(arguments.get("status")),
        )
        if updated is None:
            return {"updated": False, "item_id": item_id}
        return {"updated": True, "item": self._serialize_item(updated)}

    def _get_live_bundle_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
        default_session_id: str,
    ) -> dict[str, Any]:
        retrieval = MemoryRetrievalServiceV2(repository=repository)
        requested_session_id = self._optional_str(arguments.get("session_id"))
        limit = self._optional_int(arguments.get("limit"))
        evidence_limit_per_item = self._optional_int(arguments.get("evidence_limit_per_item"))
        memory_classes = self._optional_str_tuple(arguments.get("memory_classes"))
        statuses = self._optional_str_tuple(arguments.get("statuses"))
        bundle = retrieval.build_live_bundle(
            request=LiveMemoryBundleRequest(
                session_id=requested_session_id or default_session_id,
                query_text=self._optional_str(arguments.get("query_text")),
                intention_text=self._optional_str(arguments.get("intention_text")),
                memory_classes=memory_classes or (),
                statuses=statuses or (),
                limit=limit,
                evidence_limit_per_item=evidence_limit_per_item,
            )
        )
        return bundle.to_dict()

    def _list_conflicts_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        groups = repository.list_conflict_groups()
        limit = self._optional_int(arguments.get("limit"))
        selected = groups[:limit] if isinstance(limit, int) and limit >= 0 else groups
        return {
            "count": len(selected),
            "total_conflict_groups": len(groups),
            "groups": [group.to_dict() for group in selected],
        }

    def _get_conflict_group_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        group_key = self._required_str(arguments, "group_key")
        group = repository.get_conflict_group(group_key=group_key)
        if group is None:
            return {"found": False, "group_key": group_key}
        return {"found": True, "group": group.to_dict()}

    def _merge_items_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        target_item_id = self._required_str(arguments, "target_item_id")
        source_item_id = self._required_str(arguments, "source_item_id")
        reason = self._required_str(arguments, "reason")
        actor = self._optional_str(arguments.get("actor")) or "assistant_tool"
        suppress_source = self._optional_bool(arguments.get("suppress_source"))
        merge_result = repository.merge_items(
            target_item_id=target_item_id,
            source_item_id=source_item_id,
            actor=actor,
            reason=reason,
            suppress_source=True if suppress_source is None else suppress_source,
        )
        target_item = merge_result.get("target_item")
        source_item = merge_result.get("source_item")
        merge_event = merge_result.get("merge_event")
        return {
            "merged": True,
            "target_item": self._serialize_item(target_item) if isinstance(target_item, MemoryItem) else None,
            "source_item": self._serialize_item(source_item) if isinstance(source_item, MemoryItem) else None,
            "merge_event": dict(merge_event) if isinstance(merge_event, dict) else {},
        }

    def _suppress_conflict_side_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        reason = self._required_str(arguments, "reason")
        actor = self._optional_str(arguments.get("actor")) or "assistant_tool"
        item = repository.suppress_conflict_side(
            item_id=item_id,
            actor=actor,
            reason=reason,
        )
        if item is None:
            return {"suppressed": False, "item_id": item_id}
        return {"suppressed": True, "item": self._serialize_item(item)}

    def _suppress_item_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        updated = repository.suppress_item(
            item_id=item_id,
            note=self._optional_str(arguments.get("note")),
        )
        if updated is None:
            return {"suppressed": False, "item_id": item_id}
        return {"suppressed": True, "item": self._serialize_item(updated)}

    def _delete_item_payload(
        self,
        repository: MemoryRepositoryV2,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = self._required_str(arguments, "item_id")
        deleted = repository.delete_item(item_id=item_id)
        return {"deleted": bool(deleted), "item_id": item_id}

    @staticmethod
    def _serialize_item(item: MemoryItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "memory_class": item.memory_class,
            "scope": item.scope,
            "session_id": item.session_id,
            "status": item.status,
            "summary": item.summary,
            "structured_value": dict(item.structured_value),
            "confidence": item.confidence,
            "relevance": item.relevance,
            "maturity": item.maturity,
            "fingerprint": item.fingerprint,
            "subject_key": item.subject_key,
            "value_key": item.value_key,
            "first_seen_at_ms": item.first_seen_at_ms,
            "last_seen_at_ms": item.last_seen_at_ms,
            "last_promoted_at_ms": item.last_promoted_at_ms,
            "source_kinds": list(item.source_kinds),
            "evidence_ids": list(item.evidence_ids),
            "relation_ids": list(item.relation_ids),
            "tags": list(item.tags),
            "correction_notes": list(item.correction_notes),
            "metadata": dict(item.metadata),
        }

    @staticmethod
    def _serialize_evidence(evidence: MemoryEvidence) -> dict[str, Any]:
        return {
            "evidence_id": evidence.evidence_id,
            "evidence_kind": evidence.evidence_kind,
            "session_id": evidence.session_id,
            "source_ref": evidence.source_ref,
            "excerpt": evidence.excerpt,
            "captured_at_ms": evidence.captured_at_ms,
            "confidence": evidence.confidence,
            "item_id": evidence.item_id,
            "observation_id": evidence.observation_id,
            "candidate_id": evidence.candidate_id,
            "tags": list(evidence.tags),
            "metadata": dict(evidence.metadata),
        }

    @staticmethod
    def _required_str(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str):
            raise ValueError(f"Missing required string argument: {key}")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"Missing required string argument: {key}")
        return normalized

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        if isinstance(value, bool):
            return value
        return None

    @classmethod
    def _optional_str_tuple(cls, value: object) -> tuple[str, ...] | None:
        normalized = cls._optional_str_list(value)
        if normalized is None:
            return None
        return tuple(normalized)

    @staticmethod
    def _optional_str_list(value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if candidate:
                normalized.append(candidate)
        return normalized
