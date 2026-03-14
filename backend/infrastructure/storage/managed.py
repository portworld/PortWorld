from __future__ import annotations

import json
import logging
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Mapping

from backend.core.storage import BackendStorage
from backend.infrastructure.storage.errors import SessionNotFoundError
from backend.infrastructure.storage.postgres import PostgresMetadataStore
from backend.infrastructure.storage.types import (
    ArtifactRecord,
    MemoryExportArtifact,
    SessionMemoryResetResult,
    SessionStorageResult,
    StorageBootstrapResult,
    StorageInfo,
    VisionFrameIndexRecord,
    VisionFrameIngestResult,
    now_ms,
)
from backend.memory.lifecycle import (
    SESSION_MEMORY_JSON_FILE_NAME,
    SESSION_MEMORY_MARKDOWN_FILE_NAME,
    SHORT_TERM_MEMORY_JSON_FILE_NAME,
    SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
    VISION_EVENTS_LOG_FILE_NAME,
    VISION_ROUTING_EVENTS_LOG_FILE_NAME,
    SessionMemoryResetEligibility,
    SessionMemoryRetentionEligibility,
)
from backend.memory.profile import (
    build_profile_payload,
    build_profile_record,
    empty_profile_markdown,
    empty_profile_payload,
    parse_profile_record,
    render_profile_markdown,
)

logger = logging.getLogger(__name__)

_STORAGE_ID_PREFIX_MAX_LENGTH = 24
_UNSET = object()


def _resolve_next_retry_at_ms(
    value: int | object | None,
    existing_value: int | None,
) -> int | None:
    if isinstance(value, int):
        return value
    if value is _UNSET and existing_value is not None:
        return int(existing_value)
    return None


def _resolve_error_details_json(
    error_details: dict[str, Any] | object | None,
    error_code: str | None,
    existing_json: str | None,
) -> str | None:
    if isinstance(error_details, dict):
        return json.dumps(error_details, ensure_ascii=True, sort_keys=True)
    if error_details is None:
        return None
    if error_details is _UNSET and error_code is not None and existing_json is not None:
        return existing_json
    return None


class ManagedBackendStorage(BackendStorage):
    """Managed storage backed by Postgres metadata with object storage deferred."""

    def __init__(
        self,
        *,
        database_url: str,
        object_store_provider: str,
        object_store_bucket: str,
        object_store_prefix: str,
    ) -> None:
        self.object_store_provider = object_store_provider
        self.object_store_bucket = object_store_bucket
        self.object_store_prefix = object_store_prefix
        self.metadata_store = PostgresMetadataStore(database_url=database_url)
        super().__init__(
            storage_info=StorageInfo(
                backend="postgres_gcs",
                details={
                    "database_url_configured": bool(database_url),
                    "object_store_provider": object_store_provider,
                    "object_store_bucket": object_store_bucket,
                    "object_store_prefix": object_store_prefix,
                },
            )
        )

    def bootstrap(self) -> StorageBootstrapResult:
        self.metadata_store.initialize_schema()
        return StorageBootstrapResult(
            storage_backend=self.backend_name,
            sqlite_path=None,
            user_profile_markdown_path=None,
            user_profile_json_path=None,
            bootstrapped_at_ms=now_ms(),
            storage_details=dict(self.storage_info.details),
        )

    def bootstrap_session_storage(self, *, session_id: str) -> SessionStorageResult:
        session_storage = self.get_session_storage_paths(session_id=session_id)
        self.metadata_store.ensure_session_memory_documents(session_id=session_id)
        self._register_session_artifacts(
            session_id=session_id,
            session_storage=session_storage,
        )
        return session_storage

    def ensure_session_storage(self, *, session_id: str) -> SessionStorageResult:
        return self.bootstrap_session_storage(session_id=session_id)

    def get_session_storage_paths(self, *, session_id: str) -> SessionStorageResult:
        session_dir = Path("session") / self._storage_component_for_id(session_id)
        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=session_dir / SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
            short_term_memory_json_path=session_dir / SHORT_TERM_MEMORY_JSON_FILE_NAME,
            session_memory_markdown_path=session_dir / SESSION_MEMORY_MARKDOWN_FILE_NAME,
            session_memory_json_path=session_dir / SESSION_MEMORY_JSON_FILE_NAME,
            vision_events_log_path=session_dir / VISION_EVENTS_LOG_FILE_NAME,
            vision_routing_events_log_path=session_dir / VISION_ROUTING_EVENTS_LOG_FILE_NAME,
        )

    def register_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str | None,
        artifact_kind: str,
        artifact_path: Any,
        content_type: str,
        metadata: dict[str, Any],
    ) -> ArtifactRecord:
        relative_path = self._resolve_relative_path(artifact_path)
        created_at_ms = now_ms()
        return self.metadata_store.register_artifact_record(
            artifact_id=artifact_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            relative_path=relative_path,
            content_type=content_type,
            metadata_json=json.dumps(metadata, ensure_ascii=True, sort_keys=True),
            created_at_ms=created_at_ms,
            updated_at_ms=created_at_ms,
        )

    def read_user_profile(self) -> dict[str, object]:
        document = self.metadata_store.read_profile_document()
        return self._load_json_payload(
            document.get("json_text"),
            default_payload=empty_profile_payload(),
            context="managed profile document",
        )

    def read_user_profile_markdown(self) -> str:
        document = self.metadata_store.read_profile_document()
        markdown_text = document.get("markdown_text")
        if isinstance(markdown_text, str):
            return markdown_text
        return empty_profile_markdown()

    def write_user_profile(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        timestamp_ms = updated_at_ms if updated_at_ms is not None else now_ms()
        record = build_profile_record(
            payload,
            updated_at_ms=timestamp_ms,
            source=source,
        )
        normalized_payload = build_profile_payload(record)
        if not normalized_payload:
            return self.reset_user_profile()

        self.metadata_store.upsert_profile_document(
            json_text=json.dumps(normalized_payload, ensure_ascii=True, indent=2) + "\n",
            markdown_text=render_profile_markdown(parse_profile_record(normalized_payload)),
            updated_at_ms=timestamp_ms,
        )
        return normalized_payload

    def reset_user_profile(self) -> dict[str, object]:
        payload = empty_profile_payload()
        self.metadata_store.upsert_profile_document(
            json_text=json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            markdown_text=empty_profile_markdown(),
            updated_at_ms=now_ms(),
        )
        return payload

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        self.metadata_store.upsert_session_status(session_id=session_id, status=status)

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        self.ensure_session_storage(session_id=session_id)
        self.metadata_store.append_session_event(
            session_id=session_id,
            log_kind="vision_events",
            payload_json=json.dumps(event, ensure_ascii=True, sort_keys=True),
            created_at_ms=now_ms(),
        )

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        self.ensure_session_storage(session_id=session_id)
        self.metadata_store.append_session_event(
            session_id=session_id,
            log_kind="vision_routing_events",
            payload_json=json.dumps(event, ensure_ascii=True, sort_keys=True),
            created_at_ms=now_ms(),
        )

    def read_vision_events(self, *, session_id: str) -> list[dict[str, Any]]:
        self._require_session_persisted(session_id=session_id)
        return list(self.metadata_store.list_accepted_vision_events(session_id=session_id))

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        self._require_session_persisted(session_id=session_id)
        document = self.metadata_store.read_session_memory_document(
            session_id=session_id,
            memory_scope="session",
        )
        if document is None:
            return {}
        return self._load_json_payload(
            document.get("json_text"),
            default_payload={},
            context=f"managed session memory session_id={session_id}",
        )

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        self._require_session_persisted(session_id=session_id)
        document = self.metadata_store.read_session_memory_document(
            session_id=session_id,
            memory_scope="short_term",
        )
        if document is None:
            return {}
        return self._load_json_payload(
            document.get("json_text"),
            default_payload={},
            context=f"managed short-term memory session_id={session_id}",
        )

    def write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        self.ensure_session_storage(session_id=session_id)
        self.metadata_store.upsert_session_memory_document(
            session_id=session_id,
            memory_scope="short_term",
            json_text=json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            markdown_text=markdown_text,
            updated_at_ms=now_ms(),
        )

    def write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        self.ensure_session_storage(session_id=session_id)
        self.metadata_store.upsert_session_memory_document(
            session_id=session_id,
            memory_scope="session",
            json_text=json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            markdown_text=markdown_text,
            updated_at_ms=now_ms(),
        )

    def get_session_memory_reset_eligibility(
        self,
        *,
        session_id: str,
    ) -> SessionMemoryResetEligibility:
        counts = self.metadata_store.get_session_metadata_counts(session_id=session_id)
        session_row = self.metadata_store.get_session_row(session_id=session_id)
        has_persisted_memory = any(
            [
                bool(counts["session_row_present"]),
                int(counts["artifact_count"]) > 0,
                int(counts["vision_frame_count"]) > 0,
                int(counts["session_document_count"]) > 0,
                int(counts["session_event_count"]) > 0,
            ]
        )
        is_active = bool(session_row is not None and str(session_row["status"]) == "active")
        if is_active:
            return SessionMemoryResetEligibility(
                session_id=session_id,
                is_active=True,
                has_persisted_memory=True,
                eligible=False,
                reason="session_is_active",
            )
        if not has_persisted_memory:
            return SessionMemoryResetEligibility(
                session_id=session_id,
                is_active=False,
                has_persisted_memory=False,
                eligible=False,
                reason="session_memory_not_found",
            )
        return SessionMemoryResetEligibility(
            session_id=session_id,
            is_active=False,
            has_persisted_memory=True,
            eligible=True,
            reason="eligible",
        )

    def reset_session_memory(self, *, session_id: str) -> SessionMemoryResetResult:
        eligibility = self.get_session_memory_reset_eligibility(session_id=session_id)
        if eligibility.is_active:
            raise RuntimeError(f"Cannot reset memory for active session {session_id!r}")
        if not eligibility.has_persisted_memory:
            raise KeyError(f"No persisted memory found for session {session_id!r}")
        deleted = self.metadata_store.delete_session_metadata(session_id=session_id)
        return SessionMemoryResetResult(
            session_id=session_id,
            deleted_artifact_rows=int(deleted["deleted_artifact_rows"]),
            deleted_vision_frame_rows=int(deleted["deleted_vision_frame_rows"]),
            deleted_session_rows=int(deleted["deleted_session_rows"]),
            removed_session_dir=False,
            removed_vision_frames_dir=False,
        )

    def list_session_memory_retention_eligibility(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryRetentionEligibility]:
        if retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        reference_ms = reference_time_ms if reference_time_ms is not None else now_ms()
        cutoff_at_ms = max(0, reference_ms - retention_days * 24 * 60 * 60 * 1000)
        results: list[SessionMemoryRetentionEligibility] = []
        for row in self.metadata_store.list_session_rows_for_retention():
            session_id = str(row["session_id"])
            status = str(row["status"])
            updated_at_ms = int(row["updated_at_ms"])
            if status == "active":
                reason = "session_is_active"
                eligible = False
            elif status != "ended":
                reason = "session_not_ended"
                eligible = False
            elif updated_at_ms > cutoff_at_ms:
                reason = "within_retention_window"
                eligible = False
            else:
                reason = "expired_ended_session"
                eligible = True
            results.append(
                SessionMemoryRetentionEligibility(
                    session_id=session_id,
                    status=status,
                    updated_at_ms=updated_at_ms,
                    cutoff_at_ms=cutoff_at_ms,
                    eligible=eligible,
                    reason=reason,
                )
            )
        return results

    def sweep_expired_session_memory(
        self,
        *,
        retention_days: int,
        reference_time_ms: int | None = None,
    ) -> list[SessionMemoryResetResult]:
        results: list[SessionMemoryResetResult] = []
        for eligibility in self.list_session_memory_retention_eligibility(
            retention_days=retention_days,
            reference_time_ms=reference_time_ms,
        ):
            if eligibility.eligible:
                results.append(self.reset_session_memory(session_id=eligibility.session_id))
        return results

    def read_session_memory_status(
        self,
        *,
        session_id: str,
        recent_limit: int = 10,
    ) -> dict[str, object]:
        self._require_session_persisted(session_id=session_id)
        session_row = self.metadata_store.get_session_row(session_id=session_id)
        short_term_memory = self.read_short_term_memory(session_id=session_id)
        session_memory = self.read_session_memory(session_id=session_id)
        accepted_events = self.metadata_store.list_accepted_vision_events(session_id=session_id)
        recent_records = self.metadata_store.list_recent_vision_frame_records(
            session_id=session_id,
            limit=max(1, recent_limit),
        )
        counts = self.metadata_store.get_session_metadata_counts(session_id=session_id)

        status = (
            str(session_memory.get("status") or short_term_memory.get("status") or "")
            or ("ready" if accepted_events else "unbootstrapped")
        )
        return {
            "session_id": session_id,
            "status": status,
            "session_state": str(session_row["status"]) if session_row is not None else None,
            "session_created_at_ms": (
                int(session_row["created_at_ms"]) if session_row is not None else None
            ),
            "session_updated_at_ms": (
                int(session_row["updated_at_ms"]) if session_row is not None else None
            ),
            "accepted_event_count": len(accepted_events),
            "total_frames": int(counts["vision_frame_count"]),
            "short_term_memory": short_term_memory,
            "session_memory": session_memory,
            "recent_frames": [self._recent_frame_payload(record) for record in recent_records],
            "session_dir_exists": False,
        }

    def update_vision_frame_processing(
        self,
        *,
        session_id: str,
        frame_id: str,
        processing_status: str,
        gate_status: str | None = None,
        gate_reason: str | None = None,
        phash: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        analyzed_at_ms: int | None = None,
        next_retry_at_ms: int | object = _UNSET,
        attempt_count: int | object = _UNSET,
        error_code: str | None = None,
        error_details: dict[str, Any] | None | object = _UNSET,
        summary_snippet: str | None = None,
        routing_status: str | None = None,
        routing_reason: str | None = None,
        routing_score: float | None = None,
        routing_metadata: dict[str, Any] | None = None,
    ) -> None:
        existing = self.metadata_store.get_vision_frame_record(
            session_id=session_id,
            frame_id=frame_id,
        )
        if existing is None:
            raise KeyError(
                f"Vision frame record not found for session_id={session_id!r} frame_id={frame_id!r}"
            )
        record = VisionFrameIndexRecord(
            session_id=session_id,
            frame_id=frame_id,
            capture_ts_ms=existing.capture_ts_ms,
            ingest_ts_ms=existing.ingest_ts_ms,
            width=existing.width,
            height=existing.height,
            processing_status=processing_status,
            gate_status=gate_status,
            gate_reason=gate_reason,
            phash=phash,
            provider=provider,
            model=model,
            analyzed_at_ms=analyzed_at_ms,
            next_retry_at_ms=_resolve_next_retry_at_ms(
                next_retry_at_ms,
                existing.next_retry_at_ms,
            ),
            attempt_count=(
                int(attempt_count)
                if isinstance(attempt_count, int)
                else int(existing.attempt_count or 0)
            ),
            error_code=error_code,
            error_details_json=_resolve_error_details_json(
                error_details,
                error_code,
                existing.error_details_json,
            ),
            summary_snippet=summary_snippet,
            routing_status=routing_status,
            routing_reason=routing_reason,
            routing_score=routing_score,
            routing_metadata_json=(
                json.dumps(routing_metadata, ensure_ascii=True, sort_keys=True)
                if routing_metadata is not None
                else None
            ),
        )
        self.metadata_store.upsert_vision_frame_index(record)

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None:
        return self.metadata_store.get_vision_frame_record(
            session_id=session_id,
            frame_id=frame_id,
        )

    def store_vision_frame_ingest(
        self,
        *,
        session_id: str,
        frame_id: str,
        ts_ms: int,
        capture_ts_ms: int,
        width: int,
        height: int,
        frame_bytes: bytes,
    ) -> VisionFrameIngestResult:
        _ = (session_id, frame_id, ts_ms, capture_ts_ms, width, height, frame_bytes)
        raise self._task12_error("vision frame ingest")

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        _ = session_id
        _ = frame_id
        raise self._task12_error("vision frame artifact deletion")

    def list_memory_export_artifacts(self) -> list[MemoryExportArtifact]:
        raise self._task12_error("memory export artifacts")

    def migrate_legacy_storage_layout(self) -> dict[str, Any]:
        raise RuntimeError(
            "Storage layout migration is only supported when "
            "BACKEND_STORAGE_BACKEND=local."
        )

    def _register_session_artifacts(
        self,
        *,
        session_id: str,
        session_storage: SessionStorageResult,
    ) -> None:
        artifact_metadata = {"session_id": session_id, "artifact_role": "derived_memory"}
        self.register_artifact(
            artifact_id=f"{session_id}:short_term_memory_markdown",
            session_id=session_id,
            artifact_kind="short_term_memory_markdown",
            artifact_path=session_storage.short_term_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:short_term_memory_json",
            session_id=session_id,
            artifact_kind="short_term_memory_json",
            artifact_path=session_storage.short_term_memory_json_path,
            content_type="application/json",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:session_memory_markdown",
            session_id=session_id,
            artifact_kind="session_memory_markdown",
            artifact_path=session_storage.session_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:session_memory_json",
            session_id=session_id,
            artifact_kind="session_memory_json",
            artifact_path=session_storage.session_memory_json_path,
            content_type="application/json",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_event_log",
            session_id=session_id,
            artifact_kind="vision_event_log",
            artifact_path=session_storage.vision_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_routing_event_log",
            session_id=session_id,
            artifact_kind="vision_routing_event_log",
            artifact_path=session_storage.vision_routing_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )

    def _require_session_persisted(self, *, session_id: str) -> None:
        eligibility = self.get_session_memory_reset_eligibility(session_id=session_id)
        if not eligibility.has_persisted_memory:
            raise SessionNotFoundError(f"No persisted memory found for session {session_id!r}")

    def _resolve_relative_path(self, artifact_path: Any) -> str:
        if isinstance(artifact_path, Path):
            candidate = artifact_path.as_posix()
            if artifact_path.is_absolute():
                raise ValueError(
                    "Managed storage artifact paths must be relative logical paths, not absolute paths."
                )
            return self._validate_relative_path(candidate)
        if isinstance(artifact_path, str):
            return self._validate_relative_path(artifact_path)
        raise TypeError(f"Unsupported managed artifact path type: {type(artifact_path).__name__}")

    def _validate_relative_path(self, raw_path: str) -> str:
        candidate = raw_path.strip().replace("\\", "/")
        if not candidate or candidate.startswith("/"):
            raise ValueError("Managed storage artifact path must be a relative non-empty path.")
        if "\x00" in candidate:
            raise ValueError("Managed storage artifact path cannot contain null bytes.")
        if len(candidate) >= 2 and candidate[1] == ":" and candidate[0].isalpha():
            raise ValueError("Managed storage artifact path cannot be drive-prefixed.")
        parts = candidate.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(
                "Managed storage artifact path cannot contain empty, current-directory, "
                "or parent-directory segments."
            )
        return "/".join(parts)

    def _load_json_payload(
        self,
        raw_json: object,
        *,
        default_payload: dict[str, object],
        context: str,
    ) -> dict[str, object]:
        if not isinstance(raw_json, str):
            return dict(default_payload)
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("Failed decoding %s; returning default payload", context)
            return dict(default_payload)
        if not isinstance(payload, dict):
            logger.warning("Invalid JSON root for %s; returning default payload", context)
            return dict(default_payload)
        return payload

    def _recent_frame_payload(self, record: VisionFrameIndexRecord) -> dict[str, object]:
        error_details: dict[str, object] | None = None
        if record.error_details_json is not None:
            try:
                loaded = json.loads(record.error_details_json)
            except json.JSONDecodeError:
                loaded = {"raw": record.error_details_json}
            if isinstance(loaded, dict):
                error_details = loaded
        return {
            "frame_id": record.frame_id,
            "capture_ts_ms": record.capture_ts_ms,
            "processing_status": record.processing_status,
            "gate_status": record.gate_status,
            "gate_reason": record.gate_reason,
            "provider": record.provider,
            "model": record.model,
            "analyzed_at_ms": record.analyzed_at_ms,
            "next_retry_at_ms": record.next_retry_at_ms,
            "attempt_count": record.attempt_count,
            "error_code": record.error_code,
            "error_details": error_details,
            "routing_status": record.routing_status,
            "routing_reason": record.routing_reason,
            "routing_score": record.routing_score,
        }

    def _storage_component_for_id(self, raw_id: str) -> str:
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_id.strip())
        prefix = prefix.strip("._-") or "id"
        prefix = prefix[:_STORAGE_ID_PREFIX_MAX_LENGTH]
        digest = sha256(raw_id.encode("utf-8")).hexdigest()
        return f"{prefix}--{digest}"

    def _task12_error(self, capability: str) -> RuntimeError:
        return RuntimeError(
            f"Managed storage backend {self.backend_name!r} selected successfully, but "
            f"{capability} still require the Task 12 GCS artifact implementation."
        )
