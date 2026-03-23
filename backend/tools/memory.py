from __future__ import annotations

import logging
from dataclasses import dataclass
from json import JSONDecodeError

from backend.core.storage import RealtimeReadOnlyStorageView
from backend.memory.cross_session import parse_cross_session_markdown
from backend.tools.contracts import ToolCall, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryToolExecutor:
    storage: RealtimeReadOnlyStorageView
    memory_scope: str

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.memory_scope == "short_term":
                structured = self.storage.read_short_term_memory(session_id=call.session_id)
                markdown = self.storage.read_short_term_memory_markdown(session_id=call.session_id)
            elif self.memory_scope == "long_term":
                structured = self.storage.read_session_memory(session_id=call.session_id)
                markdown = self.storage.read_session_memory_markdown(session_id=call.session_id)
            elif self.memory_scope == "cross_session":
                read_cross_session_memory = getattr(self.storage, "read_cross_session_memory", None)
                markdown = (
                    read_cross_session_memory() if callable(read_cross_session_memory) else ""
                )
                structured = parse_cross_session_markdown(markdown) if markdown else {}
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
                    "scope": self.memory_scope,
                    "session_id": call.session_id,
                    "available": False,
                    "markdown": "",
                    "structured": {},
                },
                error_code="MEMORY_READ_FAILED",
                error_message="Memory context unavailable",
            )

        available = bool(structured)
        return ToolResult(
            ok=True,
            name=call.name,
            call_id=call.call_id,
            payload={
                "scope": self.memory_scope,
                "session_id": call.session_id,
                "available": available,
                "markdown": markdown if available else "",
                "structured": structured if available else {},
            },
        )
