from __future__ import annotations

import logging
from dataclasses import dataclass
from json import JSONDecodeError

from backend.core.storage import RealtimeReadOnlyStorageView
from backend.tools.contracts import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryToolExecutor:
    storage: RealtimeReadOnlyStorageView
    memory_scope: str

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.memory_scope == "short_term":
                context = self.storage.read_short_term_memory(session_id=call.session_id)
            elif self.memory_scope == "long_term":
                context = self.storage.read_session_memory(session_id=call.session_id)
            elif self.memory_scope == "cross_session":
                read_cross_session_memory = getattr(self.storage, "read_cross_session_memory", None)
                context = (
                    read_cross_session_memory() if callable(read_cross_session_memory) else {}
                )
            else:
                raise ValueError(f"Unsupported memory scope: {self.memory_scope}")
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "Memory tool read failed session_id=%s call_id=%s scope=%s",
                call.session_id,
                call.call_id,
                self.memory_scope,
                exc_info=exc,
            )
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={
                    "session_id": call.session_id,
                    "available": False,
                    "context": {},
                },
                error_code="MEMORY_READ_FAILED",
                error_message="Memory context unavailable",
            )

        available = bool(context)
        return ToolResult(
            ok=True,
            name=call.name,
            call_id=call.call_id,
            payload={
                "session_id": call.session_id,
                "available": available,
                "context": context if available else {},
            },
        )
