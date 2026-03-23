from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.memory.lifecycle import (
    CROSS_SESSION_MEMORY_TEMPLATE,
    MEMORY_CANDIDATES_LOG_FILE_NAME,
    SESSION_MEMORY_JSON_FILE_NAME,
    SESSION_MEMORY_MARKDOWN_FILE_NAME,
    SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
    USER_MEMORY_TEMPLATE,
    VISION_EVENTS_LOG_FILE_NAME,
    VISION_ROUTING_EVENTS_LOG_FILE_NAME,
)

_STORAGE_ID_PREFIX_MAX_LENGTH = 24


class StoragePathMixin:
    paths: Any

    def session_storage_dir(self, *, session_id: str) -> Path:
        return self._resolved_storage_dir(
            root=self.paths.session_root,
            raw_id=session_id,
        )

    def vision_frame_artifact_paths(self, *, session_id: str, frame_id: str) -> tuple[Path, Path]:
        session_dir = self.vision_frames_session_dir(session_id=session_id)
        frame_stem = self._storage_component_for_id(frame_id)
        return (
            session_dir / f"{frame_stem}.jpg",
            session_dir / f"{frame_stem}.json",
        )

    def vision_frames_session_dir(self, *, session_id: str) -> Path:
        return self._resolved_storage_dir(
            root=self.paths.vision_frames_root,
            raw_id=session_id,
        )

    def _ensure_directories(self) -> None:
        for path in (
            self.paths.data_root,
            self.paths.memory_root,
            self.paths.user_root,
            self.paths.session_root,
            self.paths.vision_frames_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._ensure_text_file(self.paths.user_memory_path, USER_MEMORY_TEMPLATE)
        self._ensure_text_file(self.paths.cross_session_memory_path, CROSS_SESSION_MEMORY_TEMPLATE)

    def _ensure_text_file(self, path: Path, default_text: str) -> bool:
        if path.exists():
            return False
        path.write_text(default_text, encoding="utf-8")
        return True

    def _build_session_storage_result(self, *, session_id: str):
        from backend.infrastructure.storage.types import SessionStorageResult

        session_dir = self.session_storage_dir(session_id=session_id)
        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=session_dir / SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
            session_memory_markdown_path=session_dir / SESSION_MEMORY_MARKDOWN_FILE_NAME,
            session_memory_json_path=session_dir / SESSION_MEMORY_JSON_FILE_NAME,
            memory_candidates_log_path=session_dir / MEMORY_CANDIDATES_LOG_FILE_NAME,
            vision_events_log_path=session_dir / VISION_EVENTS_LOG_FILE_NAME,
            vision_routing_events_log_path=session_dir / VISION_ROUTING_EVENTS_LOG_FILE_NAME,
        )

    def _session_vision_frames_dir(self, *, session_id: str) -> Path:
        return self.vision_frames_session_dir(session_id=session_id)

    def _storage_component_for_id(self, raw_id: str) -> str:
        prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_id.strip())
        prefix = prefix.strip("._-") or "id"
        prefix = prefix[:_STORAGE_ID_PREFIX_MAX_LENGTH]
        digest = sha256(raw_id.encode("utf-8")).hexdigest()
        return f"{prefix}--{digest}"

    def _resolved_storage_dir(self, *, root: Path, raw_id: str) -> Path:
        return root / self._storage_component_for_id(raw_id)
