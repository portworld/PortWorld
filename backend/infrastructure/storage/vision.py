from __future__ import annotations

import json
from typing import Any

from backend.infrastructure.storage.types import VisionFrameIndexRecord, VisionFrameIngestResult, now_ms

_UNSET = object()


def _resolve_next_retry_at_ms(
    value: int | object | None,
    existing_value: Any,
) -> int | None:
    """Resolve next_retry_at_ms with fallback to existing value if _UNSET."""
    if isinstance(value, int):
        return value
    if value is _UNSET and existing_value is not None:
        return int(existing_value)
    return None


def _resolve_error_details_json(
    error_details: dict[str, Any] | object | None,
    error_code: str | None,
    existing_json: Any,
) -> str | None:
    """Resolve error_details_json with fallback to existing value if _UNSET."""
    if isinstance(error_details, dict):
        return json.dumps(error_details, ensure_ascii=True, sort_keys=True)
    if error_details is None:
        return None
    if error_details is _UNSET and error_code is not None and existing_json is not None:
        return str(existing_json)
    return None


class VisionFrameStorageMixin:
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
        self.ensure_session_storage(session_id=session_id)
        frame_path, metadata_path = self.vision_frame_artifact_paths(
            session_id=session_id,
            frame_id=frame_id,
        )
        frame_path.parent.mkdir(parents=True, exist_ok=True)

        artifact_metadata = {
            "session_id": session_id,
            "frame_id": frame_id,
            "ts_ms": ts_ms,
            "capture_ts_ms": capture_ts_ms,
            "width": width,
            "height": height,
            "stored_bytes": len(frame_bytes),
        }
        metadata_payload = {
            **artifact_metadata,
            "stored_path": str(frame_path),
        }
        ingest_record = VisionFrameIndexRecord(
            session_id=session_id,
            frame_id=frame_id,
            capture_ts_ms=capture_ts_ms,
            ingest_ts_ms=now_ms(),
            width=width,
            height=height,
            processing_status="queued",
            gate_status=None,
            gate_reason=None,
            phash=None,
            provider=None,
            model=None,
            analyzed_at_ms=None,
            next_retry_at_ms=None,
            attempt_count=0,
            error_code=None,
            error_details_json=None,
            summary_snippet=None,
            routing_status=None,
            routing_reason=None,
            routing_score=None,
            routing_metadata_json=None,
        )

        def _cleanup_partial_files() -> None:
            for artifact_path in (metadata_path, frame_path):
                try:
                    artifact_path.unlink()
                except FileNotFoundError:
                    continue

        try:
            frame_path.write_bytes(frame_bytes)
            metadata_path.write_text(
                json.dumps(metadata_payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            _cleanup_partial_files()
            raise

        try:
            def _operation() -> None:
                with self.connect() as connection:
                    self._upsert_artifact_record(
                        artifact_id=f"{session_id}:vision_frame_jpeg:{frame_id}",
                        session_id=session_id,
                        artifact_kind="vision_frame_jpeg",
                        relative_path=str(frame_path.relative_to(self.paths.data_root)),
                        content_type="image/jpeg",
                        metadata_json=json.dumps(artifact_metadata, ensure_ascii=True, sort_keys=True),
                        created_at_ms=ingest_record.ingest_ts_ms,
                        updated_at_ms=ingest_record.ingest_ts_ms,
                        connection=connection,
                    )
                    self._upsert_artifact_record(
                        artifact_id=f"{session_id}:vision_frame_metadata:{frame_id}",
                        session_id=session_id,
                        artifact_kind="vision_frame_metadata",
                        relative_path=str(metadata_path.relative_to(self.paths.data_root)),
                        content_type="application/json",
                        metadata_json=json.dumps(artifact_metadata, ensure_ascii=True, sort_keys=True),
                        created_at_ms=ingest_record.ingest_ts_ms,
                        updated_at_ms=ingest_record.ingest_ts_ms,
                        connection=connection,
                    )
                    self._upsert_vision_frame_index(ingest_record, connection=connection)
                    connection.commit()

            self._run_with_sqlite_retry(_operation)
        except Exception:
            _cleanup_partial_files()
            raise

        return VisionFrameIngestResult(
            frame_path=frame_path,
            metadata_path=metadata_path,
            stored_bytes=len(frame_bytes),
        )

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        for artifact_path in self.vision_frame_artifact_paths(
            session_id=session_id,
            frame_id=frame_id,
        ):
            if artifact_path.exists():
                artifact_path.unlink()

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
        def _operation() -> None:
            with self.connect() as connection:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM vision_frame_index
                    WHERE session_id = ? AND frame_id = ?
                    """,
                    (session_id, frame_id),
                ).fetchone()
                if existing is None:
                    raise KeyError(
                        f"Vision frame record not found for session_id={session_id!r} frame_id={frame_id!r}"
                    )
                record = VisionFrameIndexRecord(
                    session_id=session_id,
                    frame_id=frame_id,
                    capture_ts_ms=int(existing["capture_ts_ms"]),
                    ingest_ts_ms=int(existing["ingest_ts_ms"]),
                    width=int(existing["width"]),
                    height=int(existing["height"]),
                    processing_status=processing_status,
                    gate_status=gate_status,
                    gate_reason=gate_reason,
                    phash=phash,
                    provider=provider,
                    model=model,
                    analyzed_at_ms=analyzed_at_ms,
                    next_retry_at_ms=_resolve_next_retry_at_ms(
                        next_retry_at_ms,
                        existing["next_retry_at_ms"],
                    ),
                    attempt_count=(
                        int(attempt_count)
                        if isinstance(attempt_count, int)
                        else int(existing["attempt_count"] or 0)
                    ),
                    error_code=error_code,
                    error_details_json=_resolve_error_details_json(
                        error_details,
                        error_code,
                        existing["error_details_json"],
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
                self._upsert_vision_frame_index(record, connection=connection)
                connection.commit()

        self._run_with_sqlite_retry(_operation)

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM vision_frame_index
                WHERE session_id = ? AND frame_id = ?
                """,
                (session_id, frame_id),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_vision_frame_index_record(row)
