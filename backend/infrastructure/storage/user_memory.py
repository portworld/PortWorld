from __future__ import annotations

import logging
from typing import Mapping

from backend.infrastructure.storage.types import now_ms
from backend.memory.lifecycle import CROSS_SESSION_MEMORY_TEMPLATE
from backend.memory.user_memory import (
    build_user_memory_payload,
    build_user_memory_record,
    empty_user_memory_markdown,
    parse_user_memory_markdown,
    parse_user_memory_record,
    render_user_memory_markdown,
)

logger = logging.getLogger(__name__)


class UserMemoryStorageMixin:
    def _ensure_user_memory_files(self) -> None:
        self._ensure_text_file(
            self.paths.user_memory_path,
            empty_user_memory_markdown(),
        )
        self._ensure_text_file(
            self.paths.cross_session_memory_path,
            CROSS_SESSION_MEMORY_TEMPLATE,
        )

    def read_user_memory_payload(self) -> dict[str, object]:
        path = self.paths.user_memory_path
        if not path.exists():
            path.write_text(empty_user_memory_markdown(), encoding="utf-8")
            return {}
        try:
            record = parse_user_memory_markdown(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            quarantined = path.with_name(f"{path.name}.corrupt.{now_ms()}")
            path.rename(quarantined)
            logger.warning(
                "Quarantined corrupt user memory path=%s reason=%s quarantined_path=%s",
                path,
                exc,
                quarantined,
            )
            path.write_text(empty_user_memory_markdown(), encoding="utf-8")
            return {}
        return build_user_memory_payload(record, include_metadata=False)

    def read_user_memory_markdown(self) -> str:
        self._ensure_user_memory_files()
        return self.paths.user_memory_path.read_text(encoding="utf-8")

    def read_cross_session_memory(self) -> str:
        self._ensure_user_memory_files()
        return self.paths.cross_session_memory_path.read_text(encoding="utf-8")

    def write_user_memory_payload(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        timestamp_ms = updated_at_ms if updated_at_ms is not None else now_ms()
        record = build_user_memory_record(
            payload,
            updated_at_ms=timestamp_ms,
            source=source,
        )
        normalized_payload = build_user_memory_payload(record)
        if not normalized_payload:
            return self.reset_user_memory_payload()

        self.paths.user_memory_path.write_text(
            render_user_memory_markdown(parse_user_memory_record(normalized_payload)),
            encoding="utf-8",
        )
        return normalized_payload

    def reset_user_memory_payload(self) -> dict[str, object]:
        self.paths.user_memory_path.write_text(
            empty_user_memory_markdown(),
            encoding="utf-8",
        )
        return {}

    def write_user_memory(self, *, markdown: str) -> None:
        self.paths.user_memory_path.write_text(markdown, encoding="utf-8")

    def write_cross_session_memory(self, *, markdown: str) -> None:
        self.paths.cross_session_memory_path.write_text(markdown, encoding="utf-8")
