from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from typing import Mapping

from backend.infrastructure.storage.types import now_ms
from backend.memory.profile import (
    build_profile_payload,
    build_profile_record,
    empty_profile_markdown,
    empty_profile_payload,
    parse_profile_record,
    render_profile_markdown,
)

logger = logging.getLogger(__name__)


class ProfileStorageMixin:
    def _ensure_user_profile_files(self) -> None:
        self._ensure_text_file(
            self.paths.user_profile_markdown_path,
            empty_profile_markdown(),
        )
        self._ensure_json_file(self.paths.user_profile_json_path, empty_profile_payload())

    def read_user_profile(self) -> dict[str, object]:
        path = self.paths.user_profile_json_path
        if not path.exists():
            payload = empty_profile_payload()
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            quarantined = path.with_name(f"{path.name}.corrupt.{now_ms()}")
            path.rename(quarantined)
            logger.warning(
                "Quarantined corrupt user profile path=%s reason=%s quarantined_path=%s",
                path,
                exc,
                quarantined,
            )
            payload = empty_profile_payload()
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
        if not isinstance(payload, dict):
            quarantined = path.with_name(f"{path.name}.corrupt.{now_ms()}")
            path.rename(quarantined)
            logger.warning(
                "Quarantined invalid user profile root path=%s quarantined_path=%s",
                path,
                quarantined,
            )
            payload = empty_profile_payload()
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
        return payload

    def read_user_profile_record(self):
        return parse_profile_record(self.read_user_profile())

    def read_user_profile_markdown(self) -> str:
        return self.paths.user_profile_markdown_path.read_text(encoding="utf-8")

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

        self.paths.user_profile_json_path.write_text(
            json.dumps(normalized_payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.paths.user_profile_markdown_path.write_text(
            render_profile_markdown(parse_profile_record(normalized_payload)),
            encoding="utf-8",
        )
        return normalized_payload

    def reset_user_profile(self) -> dict[str, object]:
        payload = empty_profile_payload()
        self.paths.user_profile_json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self.paths.user_profile_markdown_path.write_text(
            empty_profile_markdown(),
            encoding="utf-8",
        )
        return payload
