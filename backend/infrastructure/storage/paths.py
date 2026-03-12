from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.infrastructure.storage.types import now_ms
from backend.memory.lifecycle import (
    SESSION_MEMORY_JSON_FILE_NAME,
    SESSION_MEMORY_MARKDOWN_FILE_NAME,
    SHORT_TERM_MEMORY_JSON_FILE_NAME,
    SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
    VISION_EVENTS_LOG_FILE_NAME,
    VISION_ROUTING_EVENTS_LOG_FILE_NAME,
)

_STORAGE_ID_PREFIX_MAX_LENGTH = 24
_HASHED_DIR_PATTERN = re.compile(r".+--[0-9a-f]{64}$")


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
            self.paths.user_root,
            self.paths.session_root,
            self.paths.vision_frames_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _ensure_text_file(self, path: Path, default_text: str) -> bool:
        if path.exists():
            return False
        path.write_text(default_text, encoding="utf-8")
        return True

    def _ensure_json_file(self, path: Path, default_payload: dict[str, Any]) -> bool:
        if path.exists():
            return False
        path.write_text(
            json.dumps(default_payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return True

    def _build_session_storage_result(self, *, session_id: str):
        from backend.infrastructure.storage.types import SessionStorageResult

        session_dir = self.session_storage_dir(session_id=session_id)
        return SessionStorageResult(
            session_dir=session_dir,
            short_term_memory_markdown_path=session_dir / SHORT_TERM_MEMORY_MARKDOWN_FILE_NAME,
            short_term_memory_json_path=session_dir / SHORT_TERM_MEMORY_JSON_FILE_NAME,
            session_memory_markdown_path=session_dir / SESSION_MEMORY_MARKDOWN_FILE_NAME,
            session_memory_json_path=session_dir / SESSION_MEMORY_JSON_FILE_NAME,
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

    def _legacy_storage_component_for_id(self, raw_id: str) -> str:
        return "".join(
            char if char.isalnum() or char in "._-" else "_"
            for char in raw_id.strip()
        ) or "unknown"

    def _resolved_storage_dir(self, *, root: Path, raw_id: str) -> Path:
        return root / self._storage_component_for_id(raw_id)

    def migrate_legacy_storage_layout(self) -> dict[str, Any]:
        self._ensure_directories()
        session_ids: set[str] = set()
        with self.connect() as connection:
            for row in connection.execute(
                """
                SELECT session_id FROM session_index
                UNION
                SELECT session_id FROM artifact_index WHERE session_id IS NOT NULL
                UNION
                SELECT session_id FROM vision_frame_index
                """
            ).fetchall():
                session_ids.add(str(row["session_id"]))

        orphan_root = self.paths.data_root / "orphaned_legacy" / str(now_ms())
        migrated_count = 0
        orphaned_count = 0

        def _orphan_path(path: Path) -> None:
            nonlocal orphaned_count
            target_parent = orphan_root / path.parent.name
            target_parent.mkdir(parents=True, exist_ok=True)
            target = target_parent / path.name
            suffix = 1
            while target.exists():
                target = target_parent / f"{path.name}-{suffix}"
                suffix += 1
            path.rename(target)
            orphaned_count += 1

        for session_id in sorted(session_ids):
            for root in (self.paths.session_root, self.paths.vision_frames_root):
                legacy_dir = root / self._legacy_storage_component_for_id(session_id)
                hashed_dir = root / self._storage_component_for_id(session_id)
                if not legacy_dir.exists():
                    continue
                if hashed_dir.exists():
                    _orphan_path(legacy_dir)
                    continue
                legacy_dir.rename(hashed_dir)
                migrated_count += 1

        for root in (self.paths.session_root, self.paths.vision_frames_root):
            for candidate in root.iterdir():
                if _HASHED_DIR_PATTERN.fullmatch(candidate.name):
                    continue
                _orphan_path(candidate)

        return {
            "migrated_count": migrated_count,
            "orphaned_count": orphaned_count,
            "orphan_root": str(orphan_root),
            "session_ids_scanned": len(session_ids),
        }
