from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from json import JSONDecodeError
from typing import Any

from backend.core.storage import BackendStorage
from backend.memory.lifecycle import USER_MEMORY_METADATA_KEY, allowed_user_memory_fields
from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.results import tool_error, tool_ok

logger = logging.getLogger(__name__)


class UserMemoryMode(str, Enum):
    GET = "get"
    UPDATE = "update"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class UserMemoryToolExecutor:
    storage: BackendStorage
    mode: UserMemoryMode

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.mode is UserMemoryMode.GET:
                user_memory_payload = await asyncio.to_thread(self.storage.read_user_memory_payload)
            elif self.mode is UserMemoryMode.UPDATE:
                user_memory_payload = await asyncio.to_thread(
                    self._update_user_memory,
                    call.arguments,
                )
            elif self.mode is UserMemoryMode.COMPLETE:
                user_memory_payload = await asyncio.to_thread(self.storage.read_user_memory_payload)
            else:  # pragma: no cover - defensive for future enum changes
                raise ValueError(f"Unsupported user-memory tool mode: {self.mode}")
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "User-memory tool failed session_id=%s call_id=%s mode=%s",
                call.session_id,
                call.call_id,
                self.mode.value,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="USER_MEMORY_TOOL_FAILED",
                error_message="User memory tool failed",
                payload={
                    "session_id": call.session_id,
                    "user_memory": {},
                    "missing_fields": list(allowed_user_memory_fields()),
                },
            )

        user_memory = {
            field_name: user_memory_payload[field_name]
            for field_name in allowed_user_memory_fields()
            if field_name in user_memory_payload
        }
        present_fields = set(user_memory.keys())
        metadata = user_memory_payload.get(USER_MEMORY_METADATA_KEY)
        if not isinstance(metadata, dict):
            metadata = {}

        payload = {
            "session_id": call.session_id,
            "user_memory": user_memory,
            "missing_fields": [
                field_name
                for field_name in allowed_user_memory_fields()
                if field_name not in present_fields
            ],
            "metadata": metadata,
        }
        if self.mode is UserMemoryMode.COMPLETE:
            payload["ready"] = True
            payload["missing_required_fields"] = []

        return tool_ok(call=call, payload=payload)

    def _update_user_memory(self, arguments: dict[str, Any]) -> dict[str, object]:
        current = self.storage.read_user_memory_payload()
        merged = dict(current)

        for field_name in allowed_user_memory_fields():
            if field_name in arguments:
                merged[field_name] = arguments[field_name]

        return self.storage.write_user_memory_payload(
            payload=merged,
            source="tool_update_user_memory",
        )
