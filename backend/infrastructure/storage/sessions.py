from __future__ import annotations

import json
import logging
import re
import shutil
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from backend.infrastructure.storage.errors import SessionNotFoundError
from backend.infrastructure.storage.types import SessionMemoryResetResult, SessionStorageResult, now_ms
from backend.memory.events import AcceptedVisionEvent, coerce_accepted_vision_event
from backend.memory.lifecycle import (
    SHORT_TERM_MEMORY_TEMPLATE,
    SESSION_MEMORY_TEMPLATE,
    SessionMemoryResetEligibility,
    SessionMemoryRetentionEligibility,
)

logger = logging.getLogger(__name__)


class SessionStorageMixin:
    def bootstrap_session_storage(self, *, session_id: str) -> SessionStorageResult:
        session_dir = self.session_storage_dir(session_id=session_id)
        created_session_dir = not session_dir.exists()
        session_dir.mkdir(parents=True, exist_ok=True)
        session_storage = self._build_session_storage_result(session_id=session_id)

        created_any = created_session_dir
        created_any = self._ensure_text_file(
            session_storage.short_term_memory_markdown_path,
            SHORT_TERM_MEMORY_TEMPLATE,
        ) or created_any
        created_any = self._ensure_text_file(
            session_storage.session_memory_markdown_path,
            SESSION_MEMORY_TEMPLATE,
        ) or created_any
        created_any = self._ensure_text_file(session_storage.vision_events_log_path, "") or created_any
        created_any = (
            self._ensure_text_file(session_storage.vision_routing_events_log_path, "") or created_any
        )

        return session_storage

    def ensure_session_storage(self, *, session_id: str) -> SessionStorageResult:
        return self.bootstrap_session_storage(session_id=session_id)

    def get_session_storage_paths(self, *, session_id: str) -> SessionStorageResult:
        return self._build_session_storage_result(session_id=session_id)

    def _register_session_artifacts(
        self,
        *,
        session_id: str,
        session_storage: SessionStorageResult,
    ) -> None:
        artifact_metadata = {"session_id": session_id, "artifact_role": "derived_memory"}
        self.register_artifact(
            artifact_id=f"{session_id}:short_term_memory_markdown",
            session_id=session_id,
            artifact_kind="short_term_memory_markdown",
            artifact_path=session_storage.short_term_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:session_memory_markdown",
            session_id=session_id,
            artifact_kind="session_memory_markdown",
            artifact_path=session_storage.session_memory_markdown_path,
            content_type="text/markdown",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_event_log",
            session_id=session_id,
            artifact_kind="vision_event_log",
            artifact_path=session_storage.vision_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )
        self.register_artifact(
            artifact_id=f"{session_id}:vision_routing_event_log",
            session_id=session_id,
            artifact_kind="vision_routing_event_log",
            artifact_path=session_storage.vision_routing_events_log_path,
            content_type="application/x-ndjson",
            metadata=artifact_metadata,
        )

    def _session_has_persisted_memory(self, *, session_id: str) -> bool:
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

        return any(
            [
                session_row is not None,
                artifact_count > 0,
                vision_frame_count > 0,
                session_storage.session_dir.exists(),
                raw_vision_dir.exists(),
            ]
        )

    def _require_session_persisted(self, *, session_id: str) -> None:
        if not self._session_has_persisted_memory(session_id=session_id):
            raise SessionNotFoundError(f"No persisted memory found for session {session_id!r}")

    def _quarantine_corrupt_file(self, path: Path, *, reason: str) -> Path:
        quarantined_path = path.with_name(f"{path.name}.corrupt.{now_ms()}")
        suffix = 1
        while quarantined_path.exists():
            quarantined_path = path.with_name(f"{path.name}.corrupt.{now_ms()}.{suffix}")
            suffix += 1
        path.rename(quarantined_path)
        logger.warning(
            "Quarantined corrupt storage artifact path=%s reason=%s quarantined_path=%s",
            path,
            reason,
            quarantined_path,
        )
        return quarantined_path

    def _safe_read_jsonl_file(self, *, session_id: str, path: Path) -> list[AcceptedVisionEvent]:
        if not path.exists():
            path.write_text("", encoding="utf-8")
            return []
        valid_events: list[AcceptedVisionEvent] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "VISION_EVENT_RECORD_READ_FAILED session=%s path=%s reason=%s",
                session_id,
                path,
                exc,
            )
            return []

        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except JSONDecodeError as exc:
                logger.warning(
                    "VISION_EVENT_RECORD_SKIPPED session=%s line=%d reason=invalid_json details=%s",
                    session_id,
                    index,
                    exc,
                )
                continue
            if not isinstance(payload, dict):
                logger.warning(
                    "VISION_EVENT_RECORD_SKIPPED session=%s line=%d reason=non_object_record",
                    session_id,
                    index,
                )
                continue
            event, reason = coerce_accepted_vision_event(payload)
            if event is None:
                logger.warning(
                    "VISION_EVENT_RECORD_SKIPPED session=%s line=%d reason=%s",
                    session_id,
                    index,
                    reason or "invalid_record",
                )
                continue
            valid_events.append(event)
        return valid_events

    def upsert_session_status(self, *, session_id: str, status: str) -> None:
        timestamp_ms = now_ms()

        def _operation() -> None:
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

        self._run_with_sqlite_retry(_operation)

    def append_vision_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        session_storage = self.get_session_storage_paths(session_id=session_id)
        if not session_storage.session_dir.exists():
            session_storage = self.bootstrap_session_storage(session_id=session_id)
        with session_storage.vision_events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")

    def append_vision_routing_event(self, *, session_id: str, event: dict[str, Any]) -> None:
        session_storage = self.get_session_storage_paths(session_id=session_id)
        if not session_storage.session_dir.exists():
            session_storage = self.bootstrap_session_storage(session_id=session_id)
        with session_storage.vision_routing_events_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")

    def read_vision_events(self, *, session_id: str) -> list[AcceptedVisionEvent]:
        self._require_session_persisted(session_id=session_id)
        session_storage = self.get_session_storage_paths(session_id=session_id)
        return self._safe_read_jsonl_file(
            session_id=session_id,
            path=session_storage.vision_events_log_path,
        )

    def read_session_memory(self, *, session_id: str) -> dict[str, Any]:
        self._require_session_persisted(session_id=session_id)
        session_storage = self.get_session_storage_paths(session_id=session_id)
        return self._read_memory_markdown_payload(path=session_storage.session_memory_markdown_path)

    def read_short_term_memory(self, *, session_id: str) -> dict[str, Any]:
        self._require_session_persisted(session_id=session_id)
        session_storage = self.get_session_storage_paths(session_id=session_id)
        return self._read_memory_markdown_payload(path=session_storage.short_term_memory_markdown_path)

    def get_session_memory_reset_eligibility(
        self,
        *,
        session_id: str,
    ) -> SessionMemoryResetEligibility:
        with self.connect() as connection:
            session_row = connection.execute(
                """
                SELECT status
                FROM session_index
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        has_persisted_memory = self._session_has_persisted_memory(session_id=session_id)
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
        session_storage = self.get_session_storage_paths(session_id=session_id)
        if not session_storage.session_dir.exists():
            session_storage = self.bootstrap_session_storage(session_id=session_id)
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
        session_storage = self.get_session_storage_paths(session_id=session_id)
        if not session_storage.session_dir.exists():
            session_storage = self.bootstrap_session_storage(session_id=session_id)
        session_storage.session_memory_markdown_path.write_text(
            markdown_text,
            encoding="utf-8",
        )

    def _read_memory_markdown_payload(self, *, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}

        from backend.memory.materializer import (
            coerce_session_memory_payload,
            coerce_short_term_memory_payload,
        )

        if path.name == "SHORT_TERM.md":
            payload = coerce_short_term_memory_payload(markdown)
            payload.update(_parse_section_key_value_bullets(markdown, section_name="Recent Changes"))
            return payload
        if path.name == "LONG_TERM.md":
            payload = coerce_session_memory_payload(markdown)
            payload.update(
                _parse_section_key_value_bullets(markdown, section_name="Important Facts Learned")
            )
            pending_follow_ups = _read_section_text(markdown, "Pending Follow-Ups")
            if pending_follow_ups and pending_follow_ups.lower() != "none":
                payload["open_uncertainties"] = _split_semicolon_list(pending_follow_ups)
            return payload

        payload: dict[str, Any] = {}
        key_aliases = {
            "current_scene": "current_scene_summary",
            "visible_text": "recent_visible_text",
            "documents_seen": "recent_documents",
            "source_frames": "source_frame_ids",
        }
        lines = [line.strip() for line in markdown.splitlines() if line.strip()]
        for line in lines:
            if ":" not in line:
                continue
            normalized = line.removeprefix("- ").strip()
            key_raw, value_raw = normalized.split(":", 1)
            key = key_raw.strip().lower().replace(" ", "_")
            key = key_aliases.get(key, key)
            value = value_raw.strip()
            if not value or value.lower() == "none":
                continue
            if key in {
                "recent_entities",
                "recent_actions",
                "recent_visible_text",
                "recent_documents",
                "source_frame_ids",
                "recurring_entities",
                "documents_seen",
                "notable_transitions",
                "open_uncertainties",
            }:
                payload[key] = _parse_csv_list(value)
                continue
            if re.fullmatch(r"-?\d+", value):
                payload[key] = int(value)
                continue
            payload[key] = value
        return payload

    def read_session_memory_status(
        self,
        *,
        session_id: str,
        recent_limit: int = 10,
    ) -> dict[str, Any]:
        self._require_session_persisted(session_id=session_id)
        session_storage = self.get_session_storage_paths(session_id=session_id)
        short_term_memory = self.read_short_term_memory(session_id=session_id)
        session_memory = self.read_session_memory(session_id=session_id)
        accepted_events = self.read_vision_events(session_id=session_id)

        with self.connect() as connection:
            session_row = connection.execute(
                """
                SELECT status, created_at_ms, updated_at_ms
                FROM session_index
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            total_frames = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM vision_frame_index
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()[0]
            )
            recent_rows = connection.execute(
                """
                SELECT *
                FROM vision_frame_index
                WHERE session_id = ?
                ORDER BY capture_ts_ms DESC
                LIMIT ?
                """,
                (session_id, max(1, recent_limit)),
            ).fetchall()

        status = (
            str(session_memory.get("status") or short_term_memory.get("status") or "")
            or ("ready" if accepted_events else "unbootstrapped")
        )
        recent_frames: list[dict[str, Any]] = []
        for row in recent_rows:
            error_details: dict[str, Any] | None = None
            if row["error_details_json"] is not None:
                try:
                    loaded_error_details = json.loads(str(row["error_details_json"]))
                except JSONDecodeError:
                    loaded_error_details = {"raw": str(row["error_details_json"])}
                if isinstance(loaded_error_details, dict):
                    error_details = loaded_error_details
            recent_frames.append(
                {
                    "frame_id": str(row["frame_id"]),
                    "capture_ts_ms": int(row["capture_ts_ms"]),
                    "processing_status": str(row["processing_status"]),
                    "gate_status": str(row["gate_status"]) if row["gate_status"] is not None else None,
                    "gate_reason": str(row["gate_reason"]) if row["gate_reason"] is not None else None,
                    "provider": str(row["provider"]) if row["provider"] is not None else None,
                    "model": str(row["model"]) if row["model"] is not None else None,
                    "analyzed_at_ms": int(row["analyzed_at_ms"]) if row["analyzed_at_ms"] is not None else None,
                    "next_retry_at_ms": int(row["next_retry_at_ms"]) if row["next_retry_at_ms"] is not None else None,
                    "attempt_count": int(row["attempt_count"] or 0),
                    "error_code": str(row["error_code"]) if row["error_code"] is not None else None,
                    "error_details": error_details,
                    "routing_status": str(row["routing_status"]) if row["routing_status"] is not None else None,
                    "routing_reason": str(row["routing_reason"]) if row["routing_reason"] is not None else None,
                    "routing_score": float(row["routing_score"]) if row["routing_score"] is not None else None,
                }
            )

        return {
            "session_id": session_id,
            "status": status,
            "session_state": str(session_row["status"]) if session_row is not None else None,
            "session_created_at_ms": int(session_row["created_at_ms"]) if session_row is not None else None,
            "session_updated_at_ms": int(session_row["updated_at_ms"]) if session_row is not None else None,
            "accepted_event_count": len(accepted_events),
            "total_frames": total_frames,
            "short_term_memory": short_term_memory,
            "session_memory": session_memory,
            "recent_frames": recent_frames,
            "session_dir_exists": session_storage.session_dir.exists(),
        }

    def _delete_session_memory(self, *, session_id: str) -> SessionMemoryResetResult:
        eligibility = self.get_session_memory_reset_eligibility(session_id=session_id)
        if eligibility.is_active:
            raise RuntimeError(f"Cannot delete active session memory for {session_id!r}")

        session_storage = self._build_session_storage_result(session_id=session_id)
        raw_vision_dir = self._session_vision_frames_dir(session_id=session_id)

        staged_root = self.paths.data_root / "pending_delete" / str(now_ms())
        staged_root.mkdir(parents=True, exist_ok=True)
        staged_session_dir = staged_root / "session"
        staged_vision_dir = staged_root / "vision_frames"
        renamed_session_dir = False
        renamed_vision_dir = False

        if session_storage.session_dir.exists():
            staged_session_dir.parent.mkdir(parents=True, exist_ok=True)
            session_storage.session_dir.rename(staged_session_dir)
            renamed_session_dir = True

        if raw_vision_dir.exists():
            staged_vision_dir.parent.mkdir(parents=True, exist_ok=True)
            raw_vision_dir.rename(staged_vision_dir)
            renamed_vision_dir = True

        try:
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
        except Exception:
            if renamed_session_dir and staged_session_dir.exists() and not session_storage.session_dir.exists():
                staged_session_dir.rename(session_storage.session_dir)
            if renamed_vision_dir and staged_vision_dir.exists() and not raw_vision_dir.exists():
                staged_vision_dir.rename(raw_vision_dir)
            raise

        removed_session_dir = False
        if staged_session_dir.exists():
            shutil.rmtree(staged_session_dir)
            removed_session_dir = True

        removed_vision_frames_dir = False
        if staged_vision_dir.exists():
            shutil.rmtree(staged_vision_dir)
            removed_vision_frames_dir = True

        if staged_root.exists():
            try:
                staged_root.rmdir()
            except OSError:
                logger.warning("Pending delete directory retained path=%s", staged_root)

        return SessionMemoryResetResult(
            session_id=session_id,
            deleted_artifact_rows=max(artifact_delete.rowcount, 0),
            deleted_vision_frame_rows=max(vision_delete.rowcount, 0),
            deleted_session_rows=max(session_delete.rowcount, 0),
            removed_session_dir=removed_session_dir,
            removed_vision_frames_dir=removed_vision_frames_dir,
        )


def _parse_csv_list(raw_value: str) -> list[str]:
    values: list[str] = []
    for item in raw_value.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        values.append(candidate)
    return values


def _parse_section_key_value_bullets(markdown: str, *, section_name: str) -> dict[str, Any]:
    section_text = _read_section_text(markdown, section_name)
    payload: dict[str, Any] = {}
    key_aliases = {
        "environment_summary": "environment_summary",
        "recurring_entities": "recurring_entities",
        "documents_seen": "documents_seen",
        "notable_transitions": "notable_transitions",
        "status": "status",
        "reason": "reason",
        "provider": "provider",
        "model": "model",
        "bootstrap_frame": "bootstrap_frame_id",
        "next_retry": "next_retry_at_ms",
        "last_attempt": "last_attempt_at_ms",
        "attempt_count": "attempt_count",
        "error_code": "error_code",
        "source_frames": "source_frame_ids",
        "recent_entities": "recent_entities",
        "recent_actions": "recent_actions",
        "visible_text": "recent_visible_text",
        "documents_seen_short": "recent_documents",
        "documents_seen_recent": "recent_documents",
        "documents_seen_current": "recent_documents",
    }
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        key_raw, value_raw = line.removeprefix("- ").split(":", 1)
        key = key_raw.strip().lower().replace(" ", "_")
        canonical_key = key_aliases.get(key, key)
        if section_name == "Recent Changes" and key == "documents_seen":
            canonical_key = "recent_documents"
        value = value_raw.strip()
        if not value or value.lower() == "none":
            continue
        if canonical_key == "notable_transitions":
            payload[canonical_key] = _split_semicolon_list(value)
            continue
        if canonical_key in {
            "source_frame_ids",
            "recent_entities",
            "recent_actions",
            "recent_visible_text",
            "recent_documents",
            "recurring_entities",
            "documents_seen",
            "notable_transitions",
        }:
            payload[canonical_key] = _parse_csv_list(value)
            continue
        if re.fullmatch(r"-?\d+", value):
            payload[canonical_key] = int(value)
            continue
        payload[canonical_key] = value
    return payload


def _read_section_text(markdown: str, section_name: str) -> str:
    header = f"## {section_name}"
    lines = markdown.splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = stripped == header
            continue
        if in_section:
            collected.append(line.rstrip())
    return "\n".join(part for part in collected if part).strip()


def _split_semicolon_list(raw_value: str) -> list[str]:
    values: list[str] = []
    for item in raw_value.split(";"):
        candidate = item.strip()
        if candidate:
            values.append(candidate)
    return values
