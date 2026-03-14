from __future__ import annotations

from typing import Any, Mapping

from backend.core.storage import BackendStorage
from backend.infrastructure.storage.types import (
    SessionMemoryResetResult,
    StorageBootstrapResult,
    StorageInfo,
    now_ms,
)


class ManagedBackendStorage(BackendStorage):
    """Managed storage selection scaffold for postgres_gcs mode."""

    def __init__(
        self,
        *,
        database_url_configured: bool,
        object_store_provider: str,
        object_store_bucket: str,
        object_store_prefix: str,
    ) -> None:
        super().__init__(
            storage_info=StorageInfo(
                backend="postgres_gcs",
                details={
                    "database_url_configured": database_url_configured,
                    "object_store_provider": object_store_provider,
                    "object_store_bucket": object_store_bucket,
                    "object_store_prefix": object_store_prefix,
                },
            )
        )

    def bootstrap(self) -> StorageBootstrapResult:
        return StorageBootstrapResult(
            storage_backend=self.backend_name,
            sqlite_path=None,
            user_profile_markdown_path=None,
            user_profile_json_path=None,
            bootstrapped_at_ms=now_ms(),
            storage_details=dict(self.storage_info.details),
        )

    def sweep_expired_session_memory(
        self,
        *,
        retention_days: int,
        reference_ms: int | None = None,
    ) -> list[SessionMemoryResetResult]:
        _ = retention_days
        _ = reference_ms
        return []

    def migrate_legacy_storage_layout(self) -> dict[str, Any]:
        raise RuntimeError(
            "Storage layout migration is only supported when "
            "BACKEND_STORAGE_BACKEND=local."
        )

    def list_memory_export_artifacts(self) -> list[Any]:
        raise self._task12_error("memory export artifacts")

    def read_user_profile(self) -> dict[str, object]:
        raise self._task12_error("user profile reads")

    def read_user_profile_markdown(self) -> str:
        raise self._task12_error("user profile markdown reads")

    def write_user_profile(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        _ = payload
        _ = source
        _ = updated_at_ms
        raise self._task12_error("user profile writes")

    def reset_user_profile(self) -> dict[str, object]:
        raise self._task12_error("user profile resets")

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        _ = session_id
        raise self._task12_error("short-term memory reads")

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        _ = session_id
        raise self._task12_error("session memory reads")

    def ensure_session_storage(self, *, session_id: str):
        _ = session_id
        raise self._task11_task12_error("session storage bootstrap")

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        _ = session_id
        _ = status
        raise self._task11_error("session status writes")

    def get_session_memory_reset_eligibility(self, *, session_id: str):
        _ = session_id
        raise self._task11_error("session reset eligibility checks")

    def reset_session_memory(self, *, session_id: str):
        _ = session_id
        raise self._task11_task12_error("session memory resets")

    def list_session_memory_retention_eligibility(
        self,
        *,
        retention_days: int,
        reference_ms: int | None = None,
    ):
        _ = retention_days
        _ = reference_ms
        raise self._task11_error("retention eligibility checks")

    def write_short_term_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown: str,
    ) -> None:
        _ = session_id
        _ = payload
        _ = markdown
        raise self._task12_error("short-term memory writes")

    def write_session_memory(
        self,
        *,
        session_id: str,
        payload: dict[str, Any],
        markdown: str,
    ) -> None:
        _ = session_id
        _ = payload
        _ = markdown
        raise self._task12_error("session memory writes")

    def read_session_memory_status(self, *, session_id: str) -> dict[str, object]:
        _ = session_id
        raise self._task11_error("session status reads")

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        _ = session_id
        _ = event
        raise self._task12_error("vision event writes")

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        _ = session_id
        _ = event
        raise self._task12_error("vision routing event writes")

    def read_vision_events(self, *, session_id: str) -> list[Any]:
        _ = session_id
        raise self._task12_error("vision event reads")

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
    ):
        _ = session_id
        _ = frame_id
        _ = ts_ms
        _ = capture_ts_ms
        _ = width
        _ = height
        _ = frame_bytes
        raise self._task11_task12_error("vision frame ingest")

    def delete_vision_ingest_artifacts(self, *, session_id: str, frame_id: str) -> None:
        _ = session_id
        _ = frame_id
        raise self._task12_error("vision frame artifact deletion")

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
        next_retry_at_ms: int | object = None,
        attempt_count: int | object = None,
        error_code: str | None = None,
        error_details: dict[str, Any] | None | object = None,
        summary_snippet: str | None = None,
        routing_status: str | None = None,
        routing_reason: str | None = None,
        routing_score: float | None = None,
        routing_metadata: dict[str, Any] | None = None,
    ) -> None:
        _ = (
            session_id,
            frame_id,
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
            error_details,
            summary_snippet,
            routing_status,
            routing_reason,
            routing_score,
            routing_metadata,
        )
        raise self._task11_error("vision frame metadata updates")

    def get_vision_frame_record(self, *, session_id: str, frame_id: str):
        _ = session_id
        _ = frame_id
        raise self._task11_error("vision frame metadata reads")

    def _task11_error(self, capability: str) -> RuntimeError:
        return RuntimeError(
            f"Managed storage backend {self.backend_name!r} selected successfully, but "
            f"{capability} require the Postgres metadata implementation from Task 11."
        )

    def _task12_error(self, capability: str) -> RuntimeError:
        return RuntimeError(
            f"Managed storage backend {self.backend_name!r} selected successfully, but "
            f"{capability} require the GCS artifact implementation from Task 12."
        )

    def _task11_task12_error(self, capability: str) -> RuntimeError:
        return RuntimeError(
            f"Managed storage backend {self.backend_name!r} selected successfully, but "
            f"{capability} require the Postgres metadata implementation from Task 11 "
            "and the GCS artifact implementation from Task 12."
        )
