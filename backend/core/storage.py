from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import Iterator

SCHEMA_VERSION = "1"


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
        if not self.paths.user_profile_markdown_path.exists():
            self.paths.user_profile_markdown_path.write_text(
                "# User Profile\n\n",
                encoding="utf-8",
            )
        if not self.paths.user_profile_json_path.exists():
            self.paths.user_profile_json_path.write_text(
                json.dumps({}, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )

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
