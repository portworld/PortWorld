from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from backend.infrastructure.storage.sqlite import SCHEMA_VERSION
from backend.infrastructure.storage.types import ArtifactRecord, VisionFrameIndexRecord, now_ms

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime.
    psycopg = None
    dict_row = None

class PostgresMetadataStore:
    def __init__(self, *, database_url: str) -> None:
        self.database_url = database_url

    def initialize_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS session_index (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at_ms BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS artifact_index (
                artifact_id TEXT PRIMARY KEY,
                session_id TEXT,
                artifact_kind TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at_ms BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS vision_frame_index (
                session_id TEXT NOT NULL,
                frame_id TEXT NOT NULL,
                capture_ts_ms BIGINT NOT NULL,
                ingest_ts_ms BIGINT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                processing_status TEXT NOT NULL,
                gate_status TEXT,
                gate_reason TEXT,
                phash TEXT,
                provider TEXT,
                model TEXT,
                analyzed_at_ms BIGINT,
                next_retry_at_ms BIGINT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_details_json TEXT,
                summary_snippet TEXT,
                routing_status TEXT,
                routing_reason TEXT,
                routing_score DOUBLE PRECISION,
                routing_metadata_json TEXT,
                PRIMARY KEY(session_id, frame_id)
            )
            """,
            """
            ALTER TABLE artifact_index
            ADD COLUMN IF NOT EXISTS updated_at_ms BIGINT
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS next_retry_at_ms BIGINT
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS error_details_json TEXT
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS routing_status TEXT
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS routing_reason TEXT
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS routing_score DOUBLE PRECISION
            """,
            """
            ALTER TABLE vision_frame_index
            ADD COLUMN IF NOT EXISTS routing_metadata_json TEXT
            """,
            """
            CREATE INDEX IF NOT EXISTS artifact_index_session_id_idx
            ON artifact_index(session_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS session_index_updated_at_ms_idx
            ON session_index(updated_at_ms)
            """,
            """
            CREATE INDEX IF NOT EXISTS vision_frame_index_session_capture_idx
            ON vision_frame_index(session_id, capture_ts_ms DESC)
            """,
        )
        with self.connect() as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES(%s, %s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
                """,
                ("schema_version", SCHEMA_VERSION),
            )
            connection.commit()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if psycopg is None or dict_row is None:
            raise RuntimeError(
                "Managed storage requires psycopg[binary]. Install the backend dependencies "
                "with the Postgres driver before using BACKEND_STORAGE_BACKEND=managed."
            )
        connection = psycopg.connect(self.database_url, row_factory=dict_row)
        try:
            yield connection
        finally:
            connection.close()

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        timestamp_ms = now_ms()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_index(session_id, status, created_at_ms, updated_at_ms)
                VALUES(%s, %s, %s, %s)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=EXCLUDED.status,
                    updated_at_ms=EXCLUDED.updated_at_ms
                """,
                (session_id, status, timestamp_ms, timestamp_ms),
            )
            connection.commit()

    def get_session_row(self, *, session_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, status, created_at_ms, updated_at_ms
                FROM session_index
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_session_metadata_counts(self, *, session_id: str) -> dict[str, int | bool]:
        with self.connect() as connection:
            session_row = connection.execute(
                """
                SELECT 1 AS present
                FROM session_index
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            artifact_count_row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM artifact_index
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
            vision_frame_count_row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM vision_frame_index
                WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return {
            "session_row_present": session_row is not None,
            "artifact_count": int((artifact_count_row or {}).get("count", 0)),
            "vision_frame_count": int((vision_frame_count_row or {}).get("count", 0)),
        }

    def list_session_rows_for_retention(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, status, updated_at_ms
                FROM session_index
                ORDER BY updated_at_ms ASC, session_id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session_metadata(self, *, session_id: str) -> dict[str, int]:
        with self.connect() as connection:
            artifact_delete = connection.execute(
                """
                DELETE FROM artifact_index
                WHERE session_id = %s
                """,
                (session_id,),
            )
            vision_delete = connection.execute(
                """
                DELETE FROM vision_frame_index
                WHERE session_id = %s
                """,
                (session_id,),
            )
            session_delete = connection.execute(
                """
                DELETE FROM session_index
                WHERE session_id = %s
                """,
                (session_id,),
            )
            connection.commit()
        return {
            "deleted_artifact_rows": max(artifact_delete.rowcount, 0),
            "deleted_vision_frame_rows": max(vision_delete.rowcount, 0),
            "deleted_session_rows": max(session_delete.rowcount, 0),
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
                    created_at_ms,
                    updated_at_ms
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    session_id=EXCLUDED.session_id,
                    artifact_kind=EXCLUDED.artifact_kind,
                    relative_path=EXCLUDED.relative_path,
                    content_type=EXCLUDED.content_type,
                    metadata_json=EXCLUDED.metadata_json,
                    updated_at_ms=EXCLUDED.updated_at_ms
                """,
                (
                    artifact_id,
                    session_id,
                    artifact_kind,
                    relative_path,
                    content_type,
                    metadata_json,
                    created_at_ms,
                    updated_at_ms,
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

    def list_artifact_records_for_session(self, *, session_id: str) -> list[ArtifactRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, session_id, artifact_kind, relative_path, content_type, metadata_json, created_at_ms
                FROM artifact_index
                WHERE session_id = %s
                ORDER BY artifact_kind ASC, created_at_ms ASC, artifact_id ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._row_to_artifact_record(dict(row)) for row in rows]

    def list_artifact_records_by_kinds(
        self,
        *,
        artifact_kinds: tuple[str, ...],
    ) -> list[ArtifactRecord]:
        if not artifact_kinds:
            return []
        placeholders = ", ".join("%s" for _ in artifact_kinds)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT artifact_id, session_id, artifact_kind, relative_path, content_type, metadata_json, created_at_ms
                FROM artifact_index
                WHERE artifact_kind IN ({placeholders})
                ORDER BY CASE WHEN session_id IS NULL THEN 0 ELSE 1 END, session_id, artifact_kind, created_at_ms, artifact_id
                """,
                artifact_kinds,
            ).fetchall()
        return [self._row_to_artifact_record(dict(row)) for row in rows]

    def upsert_vision_frame_index(self, record: VisionFrameIndexRecord) -> None:
        with self.connect() as connection:
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
                    next_retry_at_ms,
                    attempt_count,
                    error_code,
                    error_details_json,
                    summary_snippet,
                    routing_status,
                    routing_reason,
                    routing_score,
                    routing_metadata_json
                )
                VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT(session_id, frame_id) DO UPDATE SET
                    capture_ts_ms=EXCLUDED.capture_ts_ms,
                    ingest_ts_ms=EXCLUDED.ingest_ts_ms,
                    width=EXCLUDED.width,
                    height=EXCLUDED.height,
                    processing_status=EXCLUDED.processing_status,
                    gate_status=EXCLUDED.gate_status,
                    gate_reason=EXCLUDED.gate_reason,
                    phash=EXCLUDED.phash,
                    provider=EXCLUDED.provider,
                    model=EXCLUDED.model,
                    analyzed_at_ms=EXCLUDED.analyzed_at_ms,
                    next_retry_at_ms=EXCLUDED.next_retry_at_ms,
                    attempt_count=EXCLUDED.attempt_count,
                    error_code=EXCLUDED.error_code,
                    error_details_json=EXCLUDED.error_details_json,
                    summary_snippet=EXCLUDED.summary_snippet,
                    routing_status=EXCLUDED.routing_status,
                    routing_reason=EXCLUDED.routing_reason,
                    routing_score=EXCLUDED.routing_score,
                    routing_metadata_json=EXCLUDED.routing_metadata_json
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
                    record.next_retry_at_ms,
                    record.attempt_count,
                    record.error_code,
                    record.error_details_json,
                    record.summary_snippet,
                    record.routing_status,
                    record.routing_reason,
                    record.routing_score,
                    record.routing_metadata_json,
                ),
            )
            connection.commit()

    def register_vision_frame_ingest(
        self,
        *,
        frame_artifact: ArtifactRecord,
        metadata_artifact: ArtifactRecord,
        ingest_record: VisionFrameIndexRecord,
    ) -> None:
        with self.connect() as connection:
            for artifact in (frame_artifact, metadata_artifact):
                connection.execute(
                    """
                    INSERT INTO artifact_index(
                        artifact_id,
                        session_id,
                        artifact_kind,
                        relative_path,
                        content_type,
                        metadata_json,
                        created_at_ms,
                        updated_at_ms
                    )
                    VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(artifact_id) DO UPDATE SET
                        session_id=EXCLUDED.session_id,
                        artifact_kind=EXCLUDED.artifact_kind,
                        relative_path=EXCLUDED.relative_path,
                        content_type=EXCLUDED.content_type,
                        metadata_json=EXCLUDED.metadata_json,
                        updated_at_ms=EXCLUDED.updated_at_ms
                    """,
                    (
                        artifact.artifact_id,
                        artifact.session_id,
                        artifact.artifact_kind,
                        artifact.relative_path,
                        artifact.content_type,
                        artifact.metadata_json,
                        artifact.created_at_ms,
                        artifact.created_at_ms,
                    ),
                )
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
                    next_retry_at_ms,
                    attempt_count,
                    error_code,
                    error_details_json,
                    summary_snippet,
                    routing_status,
                    routing_reason,
                    routing_score,
                    routing_metadata_json
                )
                VALUES(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT(session_id, frame_id) DO UPDATE SET
                    capture_ts_ms=EXCLUDED.capture_ts_ms,
                    ingest_ts_ms=EXCLUDED.ingest_ts_ms,
                    width=EXCLUDED.width,
                    height=EXCLUDED.height,
                    processing_status=EXCLUDED.processing_status,
                    gate_status=EXCLUDED.gate_status,
                    gate_reason=EXCLUDED.gate_reason,
                    phash=EXCLUDED.phash,
                    provider=EXCLUDED.provider,
                    model=EXCLUDED.model,
                    analyzed_at_ms=EXCLUDED.analyzed_at_ms,
                    next_retry_at_ms=EXCLUDED.next_retry_at_ms,
                    attempt_count=EXCLUDED.attempt_count,
                    error_code=EXCLUDED.error_code,
                    error_details_json=EXCLUDED.error_details_json,
                    summary_snippet=EXCLUDED.summary_snippet,
                    routing_status=EXCLUDED.routing_status,
                    routing_reason=EXCLUDED.routing_reason,
                    routing_score=EXCLUDED.routing_score,
                    routing_metadata_json=EXCLUDED.routing_metadata_json
                """,
                (
                    ingest_record.session_id,
                    ingest_record.frame_id,
                    ingest_record.capture_ts_ms,
                    ingest_record.ingest_ts_ms,
                    ingest_record.width,
                    ingest_record.height,
                    ingest_record.processing_status,
                    ingest_record.gate_status,
                    ingest_record.gate_reason,
                    ingest_record.phash,
                    ingest_record.provider,
                    ingest_record.model,
                    ingest_record.analyzed_at_ms,
                    ingest_record.next_retry_at_ms,
                    ingest_record.attempt_count,
                    ingest_record.error_code,
                    ingest_record.error_details_json,
                    ingest_record.summary_snippet,
                    ingest_record.routing_status,
                    ingest_record.routing_reason,
                    ingest_record.routing_score,
                    ingest_record.routing_metadata_json,
                ),
            )
            connection.commit()

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
                WHERE session_id = %s AND frame_id = %s
                """,
                (session_id, frame_id),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_vision_frame_index_record(dict(row))

    def list_recent_vision_frame_records(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[VisionFrameIndexRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM vision_frame_index
                WHERE session_id = %s
                ORDER BY capture_ts_ms DESC
                LIMIT %s
                """,
                (session_id, max(1, limit)),
            ).fetchall()
        return [self._row_to_vision_frame_index_record(dict(row)) for row in rows]

    @staticmethod
    def _row_to_vision_frame_index_record(row: dict[str, Any]) -> VisionFrameIndexRecord:
        return VisionFrameIndexRecord(
            session_id=str(row["session_id"]),
            frame_id=str(row["frame_id"]),
            capture_ts_ms=int(row["capture_ts_ms"]),
            ingest_ts_ms=int(row["ingest_ts_ms"]),
            width=int(row["width"]),
            height=int(row["height"]),
            processing_status=str(row["processing_status"]),
            gate_status=str(row["gate_status"]) if row["gate_status"] is not None else None,
            gate_reason=str(row["gate_reason"]) if row["gate_reason"] is not None else None,
            phash=str(row["phash"]) if row["phash"] is not None else None,
            provider=str(row["provider"]) if row["provider"] is not None else None,
            model=str(row["model"]) if row["model"] is not None else None,
            analyzed_at_ms=int(row["analyzed_at_ms"]) if row["analyzed_at_ms"] is not None else None,
            next_retry_at_ms=int(row["next_retry_at_ms"]) if row["next_retry_at_ms"] is not None else None,
            attempt_count=int(row["attempt_count"] or 0),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            error_details_json=(
                str(row["error_details_json"]) if row["error_details_json"] is not None else None
            ),
            summary_snippet=(
                str(row["summary_snippet"]) if row["summary_snippet"] is not None else None
            ),
            routing_status=(
                str(row["routing_status"]) if row["routing_status"] is not None else None
            ),
            routing_reason=(
                str(row["routing_reason"]) if row["routing_reason"] is not None else None
            ),
            routing_score=float(row["routing_score"]) if row["routing_score"] is not None else None,
            routing_metadata_json=(
                str(row["routing_metadata_json"])
                if row["routing_metadata_json"] is not None
                else None
            ),
        )

    @staticmethod
    def _row_to_artifact_record(row: dict[str, Any]) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(row["artifact_id"]),
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            artifact_kind=str(row["artifact_kind"]),
            relative_path=str(row["relative_path"]),
            content_type=str(row["content_type"]),
            metadata_json=str(row["metadata_json"]),
            created_at_ms=int(row["created_at_ms"]),
        )
