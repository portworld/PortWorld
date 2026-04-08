from __future__ import annotations

import json
from hashlib import sha256
import re
from typing import Any

from backend.infrastructure.storage.metadata_protocol import ManagedMetadataStore
from backend.infrastructure.storage.object_store import ObjectStore
from backend.infrastructure.storage.types import ArtifactRecord, VisionFrameIndexRecord, now_ms

_METADATA_ROOT = "_managed_metadata"
_MANIFEST_PATH = f"{_METADATA_ROOT}/manifest.json"
_SESSION_INDEX_PATH = f"{_METADATA_ROOT}/session_index.json"
_ARTIFACT_INDEX_PATH = f"{_METADATA_ROOT}/artifact_index.json"
_VISION_FRAME_INDEX_DIR = f"{_METADATA_ROOT}/vision_frame_index"
_STORAGE_ID_PREFIX_MAX_LENGTH = 24


class ObjectStoreMetadataStore(ManagedMetadataStore):
    """Managed metadata catalog persisted inside the configured object store."""

    def __init__(self, *, object_store: ObjectStore) -> None:
        self.object_store = object_store

    def initialize_schema(self) -> None:
        if not self.object_store.exists(relative_path=_MANIFEST_PATH):
            self.object_store.put_text(
                relative_path=_MANIFEST_PATH,
                content=json.dumps(
                    {
                        "metadata_backend": "object_store",
                        "initialized_at_ms": now_ms(),
                    },
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                content_type="application/json",
            )
        self._write_json_if_missing(_SESSION_INDEX_PATH, {})
        self._write_json_if_missing(_ARTIFACT_INDEX_PATH, {})

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        index = self._read_session_index()
        timestamp_ms = now_ms()
        existing = index.get(session_id)
        created_at_ms = (
            int(existing.get("created_at_ms", timestamp_ms))
            if isinstance(existing, dict)
            else timestamp_ms
        )
        index[session_id] = {
            "session_id": session_id,
            "status": status,
            "created_at_ms": created_at_ms,
            "updated_at_ms": timestamp_ms,
        }
        self._write_json(_SESSION_INDEX_PATH, index)

    def get_session_row(self, *, session_id: str) -> dict[str, Any] | None:
        row = self._read_session_index().get(session_id)
        if not isinstance(row, dict):
            return None
        return dict(row)

    def get_session_metadata_counts(self, *, session_id: str) -> dict[str, int | bool]:
        session_row = self.get_session_row(session_id=session_id)
        artifact_count = sum(
            1
            for artifact in self._read_artifact_index().values()
            if isinstance(artifact, dict) and artifact.get("session_id") == session_id
        )
        vision_frame_count = len(self._read_vision_frame_index(session_id=session_id))
        return {
            "session_row_present": session_row is not None,
            "artifact_count": artifact_count,
            "vision_frame_count": vision_frame_count,
        }

    def list_session_rows_for_retention(self) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self._read_session_index().values()
            if isinstance(row, dict)
        ]
        rows.sort(
            key=lambda row: (
                int(row.get("updated_at_ms", 0)),
                str(row.get("session_id", "")),
            )
        )
        return rows

    def delete_session_metadata(self, *, session_id: str) -> dict[str, int]:
        artifact_index = self._read_artifact_index()
        artifact_ids = [
            artifact_id
            for artifact_id, artifact in artifact_index.items()
            if isinstance(artifact, dict) and artifact.get("session_id") == session_id
        ]
        for artifact_id in artifact_ids:
            artifact_index.pop(artifact_id, None)
        self._write_json(_ARTIFACT_INDEX_PATH, artifact_index)

        frame_index = self._read_vision_frame_index(session_id=session_id)
        deleted_vision_frame_rows = len(frame_index)
        self.object_store.delete(relative_path=self._vision_frame_index_path(session_id=session_id))

        session_index = self._read_session_index()
        deleted_session_rows = 1 if session_id in session_index else 0
        session_index.pop(session_id, None)
        self._write_json(_SESSION_INDEX_PATH, session_index)

        return {
            "deleted_artifact_rows": len(artifact_ids),
            "deleted_vision_frame_rows": deleted_vision_frame_rows,
            "deleted_session_rows": deleted_session_rows,
        }

    def register_artifact_record(
        self,
        *,
        artifact_id: str,
        session_id: str | None,
        artifact_kind: str,
        relative_path: str,
        content_type: str,
        metadata_json: str,
        created_at_ms: int,
        updated_at_ms: int,
    ) -> ArtifactRecord:
        record = ArtifactRecord(
            artifact_id=artifact_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            relative_path=relative_path,
            content_type=content_type,
            metadata_json=metadata_json,
            created_at_ms=created_at_ms,
        )
        index = self._read_artifact_index()
        index[artifact_id] = {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "artifact_kind": artifact_kind,
            "relative_path": relative_path,
            "content_type": content_type,
            "metadata_json": metadata_json,
            "created_at_ms": created_at_ms,
            "updated_at_ms": updated_at_ms,
        }
        self._write_json(_ARTIFACT_INDEX_PATH, index)
        return record

    def list_artifact_records_for_session(self, *, session_id: str) -> list[ArtifactRecord]:
        records = [
            self._artifact_record_from_payload(payload)
            for payload in self._read_artifact_index().values()
            if isinstance(payload, dict) and payload.get("session_id") == session_id
        ]
        records.sort(
            key=lambda record: (
                record.artifact_kind,
                record.created_at_ms,
                record.artifact_id,
            )
        )
        return records

    def upsert_vision_frame_index(self, record: VisionFrameIndexRecord) -> None:
        index = self._read_vision_frame_index(session_id=record.session_id)
        index[record.frame_id] = self._vision_frame_payload(record)
        self._write_json(self._vision_frame_index_path(session_id=record.session_id), index)

    def register_vision_frame_ingest(
        self,
        *,
        frame_artifact: ArtifactRecord,
        metadata_artifact: ArtifactRecord,
        ingest_record: VisionFrameIndexRecord,
    ) -> None:
        self.register_artifact_record(
            artifact_id=frame_artifact.artifact_id,
            session_id=frame_artifact.session_id,
            artifact_kind=frame_artifact.artifact_kind,
            relative_path=frame_artifact.relative_path,
            content_type=frame_artifact.content_type,
            metadata_json=frame_artifact.metadata_json,
            created_at_ms=frame_artifact.created_at_ms,
            updated_at_ms=frame_artifact.created_at_ms,
        )
        self.register_artifact_record(
            artifact_id=metadata_artifact.artifact_id,
            session_id=metadata_artifact.session_id,
            artifact_kind=metadata_artifact.artifact_kind,
            relative_path=metadata_artifact.relative_path,
            content_type=metadata_artifact.content_type,
            metadata_json=metadata_artifact.metadata_json,
            created_at_ms=metadata_artifact.created_at_ms,
            updated_at_ms=metadata_artifact.created_at_ms,
        )
        self.upsert_vision_frame_index(ingest_record)

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None:
        payload = self._read_vision_frame_index(session_id=session_id).get(frame_id)
        if not isinstance(payload, dict):
            return None
        return self._vision_frame_record_from_payload(payload)

    def list_recent_vision_frame_records(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[VisionFrameIndexRecord]:
        records = [
            self._vision_frame_record_from_payload(payload)
            for payload in self._read_vision_frame_index(session_id=session_id).values()
            if isinstance(payload, dict)
        ]
        records.sort(key=lambda record: (-record.capture_ts_ms, record.frame_id))
        return records[: max(1, limit)]

    def _read_session_index(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(_SESSION_INDEX_PATH, {})
        if not isinstance(payload, dict):
            raise RuntimeError("Managed session metadata index is not a JSON object.")
        return payload

    def _read_artifact_index(self) -> dict[str, dict[str, Any]]:
        payload = self._read_json(_ARTIFACT_INDEX_PATH, {})
        if not isinstance(payload, dict):
            raise RuntimeError("Managed artifact metadata index is not a JSON object.")
        return payload

    def _read_vision_frame_index(self, *, session_id: str) -> dict[str, dict[str, Any]]:
        payload = self._read_json(self._vision_frame_index_path(session_id=session_id), {})
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Managed vision frame metadata index for session {session_id!r} is not a JSON object."
            )
        return payload

    def _vision_frame_index_path(self, *, session_id: str) -> str:
        return f"{_VISION_FRAME_INDEX_DIR}/{self._storage_component_for_id(session_id)}.json"

    def _read_json(self, relative_path: str, default: dict[str, Any]) -> dict[str, Any]:
        text = self.object_store.get_text(relative_path=relative_path)
        if text is None:
            return dict(default)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Managed metadata artifact {relative_path!r} is not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Managed metadata artifact {relative_path!r} must contain a JSON object."
            )
        return payload

    def _write_json_if_missing(self, relative_path: str, payload: dict[str, Any]) -> None:
        if self.object_store.exists(relative_path=relative_path):
            return
        self._write_json(relative_path, payload)

    def _write_json(self, relative_path: str, payload: dict[str, Any]) -> None:
        self.object_store.put_text(
            relative_path=relative_path,
            content=json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
        )

    @staticmethod
    def _storage_component_for_id(raw_id: str) -> str:
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_id.strip())
        prefix = prefix.strip("._-") or "id"
        prefix = prefix[:_STORAGE_ID_PREFIX_MAX_LENGTH]
        digest = sha256(raw_id.encode("utf-8")).hexdigest()
        return f"{prefix}--{digest}"

    @staticmethod
    def _artifact_record_from_payload(payload: dict[str, Any]) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(payload["artifact_id"]),
            session_id=str(payload["session_id"]) if payload.get("session_id") is not None else None,
            artifact_kind=str(payload["artifact_kind"]),
            relative_path=str(payload["relative_path"]),
            content_type=str(payload["content_type"]),
            metadata_json=str(payload["metadata_json"]),
            created_at_ms=int(payload["created_at_ms"]),
        )

    @staticmethod
    def _vision_frame_payload(record: VisionFrameIndexRecord) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "frame_id": record.frame_id,
            "capture_ts_ms": record.capture_ts_ms,
            "ingest_ts_ms": record.ingest_ts_ms,
            "width": record.width,
            "height": record.height,
            "processing_status": record.processing_status,
            "gate_status": record.gate_status,
            "gate_reason": record.gate_reason,
            "phash": record.phash,
            "provider": record.provider,
            "model": record.model,
            "analyzed_at_ms": record.analyzed_at_ms,
            "next_retry_at_ms": record.next_retry_at_ms,
            "attempt_count": record.attempt_count,
            "error_code": record.error_code,
            "error_details_json": record.error_details_json,
            "summary_snippet": record.summary_snippet,
            "routing_status": record.routing_status,
            "routing_reason": record.routing_reason,
            "routing_score": record.routing_score,
            "routing_metadata_json": record.routing_metadata_json,
        }

    @staticmethod
    def _vision_frame_record_from_payload(payload: dict[str, Any]) -> VisionFrameIndexRecord:
        return VisionFrameIndexRecord(
            session_id=str(payload["session_id"]),
            frame_id=str(payload["frame_id"]),
            capture_ts_ms=int(payload["capture_ts_ms"]),
            ingest_ts_ms=int(payload["ingest_ts_ms"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            processing_status=str(payload["processing_status"]),
            gate_status=str(payload["gate_status"]) if payload.get("gate_status") is not None else None,
            gate_reason=str(payload["gate_reason"]) if payload.get("gate_reason") is not None else None,
            phash=str(payload["phash"]) if payload.get("phash") is not None else None,
            provider=str(payload["provider"]) if payload.get("provider") is not None else None,
            model=str(payload["model"]) if payload.get("model") is not None else None,
            analyzed_at_ms=(
                int(payload["analyzed_at_ms"]) if payload.get("analyzed_at_ms") is not None else None
            ),
            next_retry_at_ms=(
                int(payload["next_retry_at_ms"]) if payload.get("next_retry_at_ms") is not None else None
            ),
            attempt_count=int(payload.get("attempt_count") or 0),
            error_code=str(payload["error_code"]) if payload.get("error_code") is not None else None,
            error_details_json=(
                str(payload["error_details_json"])
                if payload.get("error_details_json") is not None
                else None
            ),
            summary_snippet=(
                str(payload["summary_snippet"]) if payload.get("summary_snippet") is not None else None
            ),
            routing_status=(
                str(payload["routing_status"]) if payload.get("routing_status") is not None else None
            ),
            routing_reason=(
                str(payload["routing_reason"]) if payload.get("routing_reason") is not None else None
            ),
            routing_score=(
                float(payload["routing_score"]) if payload.get("routing_score") is not None else None
            ),
            routing_metadata_json=(
                str(payload["routing_metadata_json"])
                if payload.get("routing_metadata_json") is not None
                else None
            ),
        )
