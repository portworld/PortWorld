from __future__ import annotations

import logging
from typing import Mapping

from backend.infrastructure.storage.types import now_ms
from backend.memory.lifecycle import CROSS_SESSION_MEMORY_TEMPLATE
from backend.memory.profile import (
    build_profile_payload,
    build_profile_record,
    empty_profile_markdown,
    parse_profile_markdown,
    parse_profile_record,
    render_profile_markdown,
)

logger = logging.getLogger(__name__)


class ProfileStorageMixin:
    def _ensure_user_profile_files(self) -> None:
        self._ensure_text_file(
            self.paths.user_memory_path,
            empty_profile_markdown(),
        )
        self._ensure_text_file(
            self.paths.cross_session_memory_path,
            CROSS_SESSION_MEMORY_TEMPLATE,
        )

    def read_user_profile(self) -> dict[str, object]:
        path = self.paths.user_memory_path
        if not path.exists():
            path.write_text(empty_profile_markdown(), encoding="utf-8")
            return {}
        try:
            record = parse_profile_markdown(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            quarantined = path.with_name(f"{path.name}.corrupt.{now_ms()}")
            path.rename(quarantined)
            logger.warning(
                "Quarantined corrupt user profile path=%s reason=%s quarantined_path=%s",
                path,
                exc,
                quarantined,
            )
            path.write_text(empty_profile_markdown(), encoding="utf-8")
            return {}
        return build_profile_payload(record, include_metadata=False)

    def read_user_profile_markdown(self) -> str:
        self._ensure_user_profile_files()
        return self.paths.user_memory_path.read_text(encoding="utf-8")

    def read_cross_session_memory(self) -> str:
        self._ensure_user_profile_files()
        return self.paths.cross_session_memory_path.read_text(encoding="utf-8")

    def write_user_profile(
        self,
        *,
        payload: Mapping[str, object],
        source: str | None = None,
        updated_at_ms: int | None = None,
    ) -> dict[str, object]:
        timestamp_ms = updated_at_ms if updated_at_ms is not None else now_ms()
        record = build_profile_record(
            payload,
            updated_at_ms=timestamp_ms,
            source=source,
        )
        normalized_payload = build_profile_payload(record)
        if not normalized_payload:
            return self.reset_user_profile()

        self.paths.user_memory_path.write_text(
            render_profile_markdown(parse_profile_record(normalized_payload)),
            encoding="utf-8",
        )
        return normalized_payload

    def reset_user_profile(self) -> dict[str, object]:
        self.paths.user_memory_path.write_text(
            empty_profile_markdown(),
            encoding="utf-8",
        )
        return {}

    def write_cross_session_memory(self, *, markdown: str) -> None:
        self.paths.cross_session_memory_path.write_text(markdown, encoding="utf-8")
