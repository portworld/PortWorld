from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import Any, Iterator

SCHEMA_VERSION = "2"


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

        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=short_term_memory_markdown_path,
            short_term_memory_json_path=short_term_memory_json_path,
            session_memory_markdown_path=session_memory_markdown_path,
            session_memory_json_path=session_memory_json_path,
            vision_events_log_path=vision_events_log_path,
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
        )
        self._upsert_vision_frame_index(record)
        return record

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
                    summary_snippet
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    summary_snippet=excluded.summary_snippet
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
            "# User Profile\n\n",
        )
        self._ensure_json_file(self.paths.user_profile_json_path, {})

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
                    PRIMARY KEY(session_id, frame_id)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ("schema_version", SCHEMA_VERSION),
            )
            connection.commit()

    def _ensure_text_file(self, path: Path, default_text: str) -> None:
        if not path.exists():
            path.write_text(default_text, encoding="utf-8")

    def _ensure_json_file(self, path: Path, default_payload: dict[str, Any]) -> None:
        if not path.exists():
            path.write_text(
                json.dumps(default_payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
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
