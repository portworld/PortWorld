from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError

from backend.core.storage import RealtimeReadOnlyStorageView
from backend.tools.contracts import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class MemoryToolExecutor:
    storage: RealtimeReadOnlyStorageView
    memory_scope: str

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.memory_scope == "short_term":
                context = self.storage.read_short_term_memory(session_id=call.session_id)
            elif self.memory_scope == "session":
                context = self.storage.read_session_memory(session_id=call.session_id)
            else:
                raise ValueError(f"Unsupported memory scope: {self.memory_scope}")
        except (JSONDecodeError, OSError, ValueError) as exc:
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
                error_message=str(exc),
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
