from __future__ import annotations

from typing import Any, Protocol

from backend.infrastructure.storage.types import ArtifactRecord, VisionFrameIndexRecord


class ManagedMetadataStore(Protocol):
    def initialize_schema(self) -> None: ...

    def upsert_session_status(self, *, session_id: str, status: str) -> None: ...

    def get_session_row(self, *, session_id: str) -> dict[str, Any] | None: ...

    def get_session_metadata_counts(self, *, session_id: str) -> dict[str, int | bool]: ...

    def list_session_rows_for_retention(self) -> list[dict[str, Any]]: ...

    def delete_session_metadata(self, *, session_id: str) -> dict[str, int]: ...

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
    ) -> ArtifactRecord: ...

    def list_artifact_records_for_session(self, *, session_id: str) -> list[ArtifactRecord]: ...

    def upsert_vision_frame_index(self, record: VisionFrameIndexRecord) -> None: ...

    def register_vision_frame_ingest(
        self,
        *,
        frame_artifact: ArtifactRecord,
        metadata_artifact: ArtifactRecord,
        ingest_record: VisionFrameIndexRecord,
    ) -> None: ...

    def get_vision_frame_record(
        self,
        *,
        session_id: str,
        frame_id: str,
    ) -> VisionFrameIndexRecord | None: ...

    def list_recent_vision_frame_records(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[VisionFrameIndexRecord]: ...
