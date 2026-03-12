from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from time import sleep
from typing import Any, Callable, Iterator, TypeVar

from backend.infrastructure.storage.types import VisionFrameIndexRecord

SCHEMA_VERSION = "4"
_SQLITE_BUSY_BACKOFF_SECONDS = (0.05, 0.1, 0.2)
_SQLITE_BUSY_TIMEOUT_MS = 5_000
_SQLiteRetryResult = TypeVar("_SQLiteRetryResult")


class SQLiteStorageMixin:
    paths: Any

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
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
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
                    next_retry_at_ms INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_details_json TEXT,
                    summary_snippet TEXT,
                    routing_status TEXT,
                    routing_reason TEXT,
                    routing_score REAL,
                    routing_metadata_json TEXT,
                    PRIMARY KEY(session_id, frame_id)
                );
                """
            )
            self._ensure_artifact_index_columns(connection)
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
            ("next_retry_at_ms", "INTEGER"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("error_details_json", "TEXT"),
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

    def _ensure_artifact_index_columns(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(artifact_index)").fetchall()
        }
        if "updated_at_ms" in existing_columns:
            return
        connection.execute(
            "ALTER TABLE artifact_index ADD COLUMN updated_at_ms INTEGER"
        )
        connection.execute(
            """
            UPDATE artifact_index
            SET updated_at_ms = COALESCE(updated_at_ms, created_at_ms)
            """
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.paths.sqlite_path,
            timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def _upsert_artifact_record(
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
        connection: sqlite3.Connection,
    ) -> None:
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
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                session_id=excluded.session_id,
                artifact_kind=excluded.artifact_kind,
                relative_path=excluded.relative_path,
                content_type=excluded.content_type,
                metadata_json=excluded.metadata_json,
                updated_at_ms=excluded.updated_at_ms
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
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    next_retry_at_ms=excluded.next_retry_at_ms,
                    attempt_count=excluded.attempt_count,
                    error_code=excluded.error_code,
                    error_details_json=excluded.error_details_json,
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
            if owns_connection:
                connection.commit()
        finally:
            if owns_connection:
                connection_cm.__exit__(None, None, None)

    def _run_with_sqlite_retry(
        self,
        operation: Callable[[], _SQLiteRetryResult],
    ) -> _SQLiteRetryResult:
        """Sync-only helper. Intended for storage calls already running off the event loop."""
        for attempt_index in range(len(_SQLITE_BUSY_BACKOFF_SECONDS) + 1):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if not self._is_sqlite_busy_error(exc) or attempt_index >= len(_SQLITE_BUSY_BACKOFF_SECONDS):
                    raise
                sleep(_SQLITE_BUSY_BACKOFF_SECONDS[attempt_index])
        return operation()

    @staticmethod
    def _is_sqlite_busy_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).strip().lower()
        return "database is locked" in message or "database is busy" in message

    def _row_to_vision_frame_index_record(self, row: sqlite3.Row) -> VisionFrameIndexRecord:
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
            error_details_json=str(row["error_details_json"]) if row["error_details_json"] is not None else None,
            summary_snippet=str(row["summary_snippet"]) if row["summary_snippet"] is not None else None,
            routing_status=str(row["routing_status"]) if row["routing_status"] is not None else None,
            routing_reason=str(row["routing_reason"]) if row["routing_reason"] is not None else None,
            routing_score=float(row["routing_score"]) if row["routing_score"] is not None else None,
            routing_metadata_json=str(row["routing_metadata_json"]) if row["routing_metadata_json"] is not None else None,
        )
