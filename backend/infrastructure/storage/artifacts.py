from __future__ import annotations

import json
from typing import Any

from backend.infrastructure.storage.types import ArtifactRecord, MemoryExportArtifact, now_ms
from backend.memory.lifecycle import EXPORTABLE_SESSION_ARTIFACT_KINDS, PROFILE_ARTIFACT_FILE_NAMES


class ArtifactStorageMixin:
    def register_artifact(
        self,
        *,
        artifact_id: str,
        session_id: str | None,
        artifact_kind: str,
        artifact_path,
        content_type: str,
        metadata: dict[str, Any],
    ) -> ArtifactRecord:
        relative_path = str(artifact_path.relative_to(self.paths.data_root))
        created_at_ms = now_ms()
        updated_at_ms = created_at_ms
        metadata_json = json.dumps(metadata, ensure_ascii=True, sort_keys=True)

        def _operation() -> None:
            with self.connect() as connection:
                self._upsert_artifact_record(
                    artifact_id=artifact_id,
                    session_id=session_id,
                    artifact_kind=artifact_kind,
                    relative_path=relative_path,
                    content_type=content_type,
                    metadata_json=metadata_json,
                    created_at_ms=created_at_ms,
                    updated_at_ms=updated_at_ms,
                    connection=connection,
                )
                connection.commit()

        self._run_with_sqlite_retry(_operation)
        return ArtifactRecord(
            artifact_id=artifact_id,
            session_id=session_id,
            artifact_kind=artifact_kind,
            relative_path=relative_path,
            content_type=content_type,
            metadata_json=metadata_json,
            created_at_ms=created_at_ms,
        )

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
