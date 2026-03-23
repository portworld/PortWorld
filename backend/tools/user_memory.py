from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from backend.core.storage import BackendStorage
from backend.memory.lifecycle import USER_MEMORY_METADATA_KEY, allowed_user_memory_fields
from backend.tools.contracts import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserMemoryToolExecutor:
    storage: BackendStorage
    mode: str

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.mode == "get":
                user_memory_payload = await asyncio.to_thread(self.storage.read_user_memory_payload)
            elif self.mode == "update":
                user_memory_payload = await asyncio.to_thread(
                    self._update_user_memory,
                    call.arguments,
                )
            elif self.mode == "complete":
                user_memory_payload = await asyncio.to_thread(self.storage.read_user_memory_payload)
            else:
                raise ValueError(f"Unsupported user-memory tool mode: {self.mode}")
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "User-memory tool failed session_id=%s call_id=%s mode=%s",
                call.session_id,
                call.call_id,
                self.mode,
                exc_info=exc,
            )
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={
                    "session_id": call.session_id,
                    "user_memory": {},
                    "missing_fields": list(allowed_user_memory_fields()),
                },
                error_code="USER_MEMORY_TOOL_FAILED",
                error_message="User memory tool failed",
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
        if self.mode == "complete":
            payload["ready"] = True
            payload["missing_required_fields"] = []

        return ToolResult(
            ok=True,
            name=call.name,
            call_id=call.call_id,
            payload=payload,
        )

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


ProfileToolExecutor = UserMemoryToolExecutor
