from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import Any, Iterator, Mapping

from backend.memory.lifecycle import (
    EXPORTABLE_SESSION_ARTIFACT_KINDS,
    PROFILE_ARTIFACT_FILE_NAMES,
    SESSION_MEMORY_ARTIFACT_FILE_NAMES,
    ProfileRecord,
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

SCHEMA_VERSION = "3"


def now_ms() -> int:
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class StoragePaths:
    data_root: Path
    user_root: Path
    session_root: Path
    vision_frames_root: Path
    debug_audio_root: Path
    sqlite_path: Path
    user_profile_markdown_path: Path
    user_profile_json_path: Path


@dataclass(frozen=True, slots=True)
class StorageBootstrapResult:
    sqlite_path: Path
    user_profile_markdown_path: Path
    user_profile_json_path: Path
    bootstrapped_at_ms: int


@dataclass(frozen=True, slots=True)
class SessionStorageResult:
    session_dir: Path
    short_term_memory_markdown_path: Path
    short_term_memory_json_path: Path
    session_memory_markdown_path: Path
    session_memory_json_path: Path
    vision_events_log_path: Path
    vision_routing_events_log_path: Path


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    session_id: str | None
    artifact_kind: str
    relative_path: str
    content_type: str
    metadata_json: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class VisionFrameIndexRecord:
    session_id: str
    frame_id: str
    capture_ts_ms: int
    ingest_ts_ms: int
    width: int
    height: int
    processing_status: str
    gate_status: str | None
    gate_reason: str | None
    phash: str | None
    provider: str | None
    model: str | None
    analyzed_at_ms: int | None
    error_code: str | None
    summary_snippet: str | None
    routing_status: str | None
    routing_reason: str | None
    routing_score: float | None
    routing_metadata_json: str | None


@dataclass(frozen=True, slots=True)
class MemoryExportArtifact:
    artifact_id: str | None
    session_id: str | None
    artifact_kind: str
    relative_path: str
    absolute_path: Path
    content_type: str
    created_at_ms: int | None


@dataclass(frozen=True, slots=True)
class SessionMemoryResetResult:
    session_id: str
    deleted_artifact_rows: int
    deleted_vision_frame_rows: int
    deleted_session_rows: int
    removed_session_dir: bool
    removed_vision_frames_dir: bool


@dataclass(frozen=True, slots=True)
class RealtimeReadOnlyStorageView:
    _storage: "BackendStorage"

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        return self._storage.read_short_term_memory(session_id=session_id)

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        return self._storage.read_session_memory(session_id=session_id)

    def read_user_profile(self) -> dict[str, Any]:
        return self._storage.read_user_profile()


class BackendStorage:
    def __init__(self, *, paths: StoragePaths) -> None:
        self.paths = paths

    def bootstrap(self) -> StorageBootstrapResult:
        self._ensure_directories()
        self._ensure_user_profile_files()
        self._initialize_sqlite()
        return StorageBootstrapResult(
            sqlite_path=self.paths.sqlite_path,
            user_profile_markdown_path=self.paths.user_profile_markdown_path,
            user_profile_json_path=self.paths.user_profile_json_path,
            bootstrapped_at_ms=now_ms(),
        )

    def ensure_session_storage(self, *, session_id: str) -> SessionStorageResult:
        session_dir = self.paths.session_root / self._sanitize_session_id(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        short_term_memory_markdown_path = session_dir / "short_term_memory.md"
        short_term_memory_json_path = session_dir / "short_term_memory.json"
        session_memory_markdown_path = session_dir / "session_memory.md"
        session_memory_json_path = session_dir / "session_memory.json"
        vision_events_log_path = session_dir / "vision_events.jsonl"
        vision_routing_events_log_path = session_dir / "vision_routing_events.jsonl"

        self._ensure_text_file(
            short_term_memory_markdown_path,
            "# Short-Term Visual Memory\n\n",
        )
        self._ensure_json_file(short_term_memory_json_path, {})
        self._ensure_text_file(
            session_memory_markdown_path,
            "# Session Memory\n\n",
        )
        self._ensure_json_file(session_memory_json_path, {})
        self._ensure_text_file(vision_events_log_path, "")
        self._ensure_text_file(vision_routing_events_log_path, "")

        artifact_metadata = {"session_id": session_id, "artifact_role": "derived_memory"}
        self.register_artifact(
            artifact_id=f"{session_id}:short_term_memory_markdown",
            session_id=session_id,
            artifact_kind="short_term_memory_markdown",
            artifact_path=short_term_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:short_term_memory_json",
            session_id=session_id,
            artifact_kind="short_term_memory_json",
            artifact_path=short_term_memory_json_path,
            content_type="application/json",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:session_memory_markdown",
            session_id=session_id,
            artifact_kind="session_memory_markdown",
            artifact_path=session_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:session_memory_json",
            session_id=session_id,
            artifact_kind="session_memory_json",
            artifact_path=session_memory_json_path,
            content_type="application/json",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_event_log",
            session_id=session_id,
            artifact_kind="vision_event_log",
            artifact_path=vision_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_routing_event_log",
            session_id=session_id,
            artifact_kind="vision_routing_event_log",
            artifact_path=vision_routing_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )

        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=short_term_memory_markdown_path,
            short_term_memory_json_path=short_term_memory_json_path,
            session_memory_markdown_path=session_memory_markdown_path,
            session_memory_json_path=session_memory_json_path,
            vision_events_log_path=vision_events_log_path,
            vision_routing_events_log_path=vision_routing_events_log_path,
        )

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        timestamp_ms = now_ms()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_index(session_id, status, created_at_ms, updated_at_ms)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (session_id, status, timestamp_ms, timestamp_ms),
            )
            connection.commit()

    def register_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str | None,
        artifact_kind: str,
        artifact_path: Path,
        content_type: str,
        metadata: dict[str, Any],
    ) -> ArtifactRecord:
        relative_path = str(artifact_path.relative_to(self.paths.data_root))
        created_at_ms = now_ms()
        metadata_json = json.dumps(metadata, ensure_ascii=True, sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifact_index(
                    artifact_id,
                    session_id,
                    artifact_kind,
                    relative_path,
                    content_type,
                    metadata_json,
                    created_at_ms
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    artifact_kind=excluded.artifact_kind,
                    relative_path=excluded.relative_path,
                    content_type=excluded.content_type,
                    metadata_json=excluded.metadata_json,
                    created_at_ms=excluded.created_at_ms
                """,
                (
                    artifact_id,
                    session_id,
                    artifact_kind,
                    relative_path,
                    content_type,
                    metadata_json,
                    created_at_ms,
                ),
            )
            connection.commit()
        return ArtifactRecord(
            artifact_id=artifact_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            relative_path=relative_path,
            content_type=content_type,
            metadata_json=metadata_json,
            created_at_ms=created_at_ms,
        )

    def record_vision_frame_ingest(
        self,
        *,
        session_id: str,
        frame_id: str,
        capture_ts_ms: int,
        width: int,
        height: int,
        ingest_ts_ms: int | None = None,
    ) -> VisionFrameIndexRecord:
        record = VisionFrameIndexRecord(
            session_id=session_id,
            frame_id=frame_id,
            capture_ts_ms=capture_ts_ms,
            ingest_ts_ms=ingest_ts_ms or now_ms(),
            width=width,
            height=height,
            processing_status="queued",
            gate_status=None,
            gate_reason=None,
            phash=None,
            provider=None,
            model=None,
            analyzed_at_ms=None,
            error_code=None,
            summary_snippet=None,
            routing_status=None,
            routing_reason=None,
            routing_score=None,
            routing_metadata_json=None,
        )
        self._upsert_vision_frame_index(record)
        return record

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        session_storage = self.ensure_session_storage(session_id=session_id)
        with session_storage.vision_events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        session_storage = self.ensure_session_storage(session_id=session_id)
        with session_storage.vision_routing_events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")

    def read_vision_events(self, *, session_id: str) -> list[dict[str, Any]]:
        session_storage = self.ensure_session_storage(session_id=session_id)
        events: list[dict[str, Any]] = []
        for line in session_storage.vision_events_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(json.loads(line))
        return events

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        session_storage = self.ensure_session_storage(session_id=session_id)
        return json.loads(session_storage.session_memory_json_path.read_text(encoding="utf-8"))

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        session_storage = self.ensure_session_storage(session_id=session_id)
        return json.loads(session_storage.short_term_memory_json_path.read_text(encoding="utf-8"))

    def read_user_profile(self) -> dict[str, Any]:
        return json.loads(self.paths.user_profile_json_path.read_text(encoding="utf-8"))

    def read_user_profile_record(self) -> ProfileRecord:
        return parse_profile_record(self.read_user_profile())

    def read_user_profile_markdown(self) -> str:
        return self.paths.user_profile_markdown_path.read_text(encoding="utf-8")

    def realtime_read_only_view(self) -> RealtimeReadOnlyStorageView:
        return RealtimeReadOnlyStorageView(self)

    def write_user_profile(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, Any]:
        timestamp_ms = updated_at_ms if updated_at_ms is not None else now_ms()
        record = build_profile_record(
            payload,
            updated_at_ms=timestamp_ms,
            source=source,
        )
        normalized_payload = build_profile_payload(record)
        if not normalized_payload:
            return self.reset_user_profile()

        self.paths.user_profile_json_path.write_text(
            json.dumps(normalized_payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.paths.user_profile_markdown_path.write_text(
            render_profile_markdown(parse_profile_record(normalized_payload)),
            encoding="utf-8",
        )
        return normalized_payload

    def reset_user_profile(self) -> dict[str, Any]:
        payload = empty_profile_payload()
        self.paths.user_profile_json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.paths.user_profile_markdown_path.write_text(
            empty_profile_markdown(),
            encoding="utf-8",
        )
        return payload

    def list_memory_export_artifacts(self) -> list[MemoryExportArtifact]:
        artifacts: list[MemoryExportArtifact] = []
        profile_artifacts = (
            (
                "user_profile_markdown",
                self.paths.user_root / PROFILE_ARTIFACT_FILE_NAMES[0],
                "text/markdown",
            ),
            (
                "user_profile_json",
                self.paths.user_root / PROFILE_ARTIFACT_FILE_NAMES[1],
                "application/json",
            ),
        )
        for artifact_kind, artifact_path, content_type in profile_artifacts:
            if not artifact_path.exists():
                continue
            artifacts.append(
                MemoryExportArtifact(
                    artifact_id=None,
                    session_id=None,
                    artifact_kind=artifact_kind,
                    relative_path=str(artifact_path.relative_to(self.paths.data_root)),
                    absolute_path=artifact_path,
                    content_type=content_type,
                    created_at_ms=None,
                )
            )

        placeholders = ", ".join("?" for _ in EXPORTABLE_SESSION_ARTIFACT_KINDS)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT artifact_id, session_id, artifact_kind, relative_path, content_type, created_at_ms
                FROM artifact_index
                WHERE artifact_kind IN ({placeholders})
                ORDER BY CASE WHEN session_id IS NULL THEN 0 ELSE 1 END, session_id, artifact_kind
                """,
                EXPORTABLE_SESSION_ARTIFACT_KINDS,
            ).fetchall()

        for row in rows:
            absolute_path = self.paths.data_root / str(row["relative_path"])
            if not absolute_path.exists():
                continue
            artifacts.append(
                MemoryExportArtifact(
                    artifact_id=str(row["artifact_id"]),
                    session_id=str(row["session_id"]) if row["session_id"] is not None else None,
                    artifact_kind=str(row["artifact_kind"]),
                    relative_path=str(row["relative_path"]),
                    absolute_path=absolute_path,
                    content_type=str(row["content_type"]),
                    created_at_ms=int(row["created_at_ms"]),
                )
            )
        return artifacts

    def get_session_memory_reset_eligibility(
        self,
        *,
        session_id: str,
    ) -> SessionMemoryResetEligibility:
        session_storage = self._build_session_storage_result(session_id=session_id)
        raw_vision_dir = self._session_vision_frames_dir(session_id=session_id)
        with self.connect() as connection:
            session_row = connection.execute(
                """
                SELECT status
                FROM session_index
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            artifact_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM artifact_index
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()[0]
            )
            vision_frame_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM vision_frame_index
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()[0]
            )

        has_persisted_memory = any(
            [
                session_row is not None,
                artifact_count > 0,
                vision_frame_count > 0,
                session_storage.session_dir.exists(),
                raw_vision_dir.exists(),
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
        return self._delete_session_memory(session_id=session_id)

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
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, status, updated_at_ms
                FROM session_index
                ORDER BY updated_at_ms ASC, session_id ASC
                """
            ).fetchall()

        results: list[SessionMemoryRetentionEligibility] = []
        for row in rows:
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
            if not eligibility.eligible:
                continue
            results.append(self._delete_session_memory(session_id=eligibility.session_id))
        return results

    def write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        session_storage = self.ensure_session_storage(session_id=session_id)
        session_storage.short_term_memory_json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        session_storage.short_term_memory_markdown_path.write_text(
            markdown_text,
            encoding="utf-8",
        )

    def write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown_text: str,
    ) -> None:
        session_storage = self.ensure_session_storage(session_id=session_id)
        session_storage.session_memory_json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        session_storage.session_memory_markdown_path.write_text(
            markdown_text,
            encoding="utf-8",
        )

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        session_component = self._sanitize_session_id(session_id)
        frame_component = self._sanitize_session_id(frame_id)
        for suffix in (".jpg", ".json"):
            artifact_path = self.paths.vision_frames_root / session_component / f"{frame_component}{suffix}"
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
        error_code: str | None = None,
        summary_snippet: str | None = None,
        routing_status: str | None = None,
        routing_reason: str | None = None,
        routing_score: float | None = None,
        routing_metadata: dict[str, Any] | None = None,
    ) -> None:
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
                error_code=error_code,
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

    def _upsert_vision_frame_index(
        self,
        record: VisionFrameIndexRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        if connection is None:
            connection_cm = self.connect()
            connection = connection_cm.__enter__()
        try:
            connection.execute(
                """
                INSERT INTO vision_frame_index(
                    session_id,
                    frame_id,
                    capture_ts_ms,
                    ingest_ts_ms,
                    width,
                    height,
                    processing_status,
                    gate_status,
                    gate_reason,
                    phash,
                    provider,
                    model,
                    analyzed_at_ms,
                    error_code,
                    summary_snippet,
                    routing_status,
                    routing_reason,
                    routing_score,
                    routing_metadata_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, frame_id) DO UPDATE SET
                    capture_ts_ms=excluded.capture_ts_ms,
                    ingest_ts_ms=excluded.ingest_ts_ms,
                    width=excluded.width,
                    height=excluded.height,
                    processing_status=excluded.processing_status,
                    gate_status=excluded.gate_status,
                    gate_reason=excluded.gate_reason,
                    phash=excluded.phash,
                    provider=excluded.provider,
                    model=excluded.model,
                    analyzed_at_ms=excluded.analyzed_at_ms,
                    error_code=excluded.error_code,
                    summary_snippet=excluded.summary_snippet,
                    routing_status=excluded.routing_status,
                    routing_reason=excluded.routing_reason,
                    routing_score=excluded.routing_score,
                    routing_metadata_json=excluded.routing_metadata_json
                """,
                (
                    record.session_id,
                    record.frame_id,
                    record.capture_ts_ms,
                    record.ingest_ts_ms,
                    record.width,
                    record.height,
                    record.processing_status,
                    record.gate_status,
                    record.gate_reason,
                    record.phash,
                    record.provider,
                    record.model,
                    record.analyzed_at_ms,
                    record.error_code,
                    record.summary_snippet,
                    record.routing_status,
                    record.routing_reason,
                    record.routing_score,
                    record.routing_metadata_json,
                ),
            )
            if owns_connection:
                connection.commit()
        finally:
            if owns_connection:
                connection_cm.__exit__(None, None, None)

    def _ensure_directories(self) -> None:
        for path in (
            self.paths.data_root,
            self.paths.user_root,
            self.paths.session_root,
            self.paths.vision_frames_root,
            self.paths.debug_audio_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _ensure_user_profile_files(self) -> None:
        self._ensure_text_file(
            self.paths.user_profile_markdown_path,
            empty_profile_markdown(),
        )
        self._ensure_json_file(self.paths.user_profile_json_path, empty_profile_payload())

    def _initialize_sqlite(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS session_index (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifact_index (
                    artifact_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    artifact_kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vision_frame_index (
                    session_id TEXT NOT NULL,
                    frame_id TEXT NOT NULL,
                    capture_ts_ms INTEGER NOT NULL,
                    ingest_ts_ms INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    processing_status TEXT NOT NULL,
                    gate_status TEXT,
                    gate_reason TEXT,
                    phash TEXT,
                    provider TEXT,
                    model TEXT,
                    analyzed_at_ms INTEGER,
                    error_code TEXT,
                    summary_snippet TEXT,
                    routing_status TEXT,
                    routing_reason TEXT,
                    routing_score REAL,
                    routing_metadata_json TEXT,
                    PRIMARY KEY(session_id, frame_id)
                );
                """
            )
            self._ensure_vision_frame_index_columns(connection)
            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("schema_version", SCHEMA_VERSION),
            )
            connection.commit()

    def _ensure_vision_frame_index_columns(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(vision_frame_index)").fetchall()
        }
        required_columns = (
            ("routing_status", "TEXT"),
            ("routing_reason", "TEXT"),
            ("routing_score", "REAL"),
            ("routing_metadata_json", "TEXT"),
        )
        for column_name, column_type in required_columns:
            if column_name in existing_columns:
                continue
            connection.execute(
                f"ALTER TABLE vision_frame_index ADD COLUMN {column_name} {column_type}"
            )

    def _ensure_text_file(self, path: Path, default_text: str) -> None:
        if not path.exists():
            path.write_text(default_text, encoding="utf-8")

    def _ensure_json_file(self, path: Path, default_payload: dict[str, Any]) -> None:
        if not path.exists():
            path.write_text(
                json.dumps(default_payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )

    def _build_session_storage_result(self, *, session_id: str) -> SessionStorageResult:
        session_dir = self.paths.session_root / self._sanitize_session_id(session_id)
        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[0],
            short_term_memory_json_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[1],
            session_memory_markdown_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[2],
            session_memory_json_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[3],
            vision_events_log_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[4],
            vision_routing_events_log_path=session_dir / SESSION_MEMORY_ARTIFACT_FILE_NAMES[5],
        )

    def _session_vision_frames_dir(self, *, session_id: str) -> Path:
        return self.paths.vision_frames_root / self._sanitize_session_id(session_id)

    def _delete_session_memory(self, *, session_id: str) -> SessionMemoryResetResult:
        eligibility = self.get_session_memory_reset_eligibility(session_id=session_id)
        if eligibility.is_active:
            raise RuntimeError(f"Cannot delete active session memory for {session_id!r}")

        session_storage = self._build_session_storage_result(session_id=session_id)
        raw_vision_dir = self._session_vision_frames_dir(session_id=session_id)
        with self.connect() as connection:
            artifact_delete = connection.execute(
                """
                DELETE FROM artifact_index
                WHERE session_id = ?
                """,
                (session_id,),
            )
            vision_delete = connection.execute(
                """
                DELETE FROM vision_frame_index
                WHERE session_id = ?
                """,
                (session_id,),
            )
            session_delete = connection.execute(
                """
                DELETE FROM session_index
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.commit()

        removed_session_dir = False
        if session_storage.session_dir.exists():
            shutil.rmtree(session_storage.session_dir)
            removed_session_dir = True

        removed_vision_frames_dir = False
        if raw_vision_dir.exists():
            shutil.rmtree(raw_vision_dir)
            removed_vision_frames_dir = True

        return SessionMemoryResetResult(
            session_id=session_id,
            deleted_artifact_rows=max(artifact_delete.rowcount, 0),
            deleted_vision_frame_rows=max(vision_delete.rowcount, 0),
            deleted_session_rows=max(session_delete.rowcount, 0),
            removed_session_dir=removed_session_dir,
            removed_vision_frames_dir=removed_vision_frames_dir,
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.paths.sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def _sanitize_session_id(self, session_id: str) -> str:
        return "".join(
            char if char.isalnum() or char in "._-" else "_"
            for char in session_id.strip()
        ) or "unknown"
