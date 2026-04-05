from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from json import JSONDecodeError

from backend.core.storage import RealtimeReadOnlyStorageView
from backend.memory.cross_session import parse_cross_session_markdown
from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.results import tool_error, tool_ok

logger = logging.getLogger(__name__)


class MemoryScope(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    CROSS_SESSION = "cross_session"


@dataclass(frozen=True, slots=True)
class MemoryToolExecutor:
    storage: RealtimeReadOnlyStorageView
    memory_scope: MemoryScope

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            if self.memory_scope is MemoryScope.SHORT_TERM:
                structured = self.storage.read_short_term_memory(session_id=call.session_id)
                markdown = self.storage.read_short_term_memory_markdown(session_id=call.session_id)
            elif self.memory_scope is MemoryScope.LONG_TERM:
                structured = self.storage.read_session_memory(session_id=call.session_id)
                markdown = self.storage.read_session_memory_markdown(session_id=call.session_id)
            elif self.memory_scope is MemoryScope.CROSS_SESSION:
                markdown = self.storage.read_cross_session_memory()
                structured = parse_cross_session_markdown(markdown) if markdown else {}
            else:  # pragma: no cover - defensive for future enum changes
                raise ValueError(f"Unsupported memory scope: {self.memory_scope}")
        except (JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(
                "Memory tool read failed session_id=%s call_id=%s scope=%s",
                call.session_id,
                call.call_id,
                self.memory_scope.value,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="MEMORY_READ_FAILED",
                error_message="Memory context unavailable",
                payload={
                    "scope": self.memory_scope.value,
                    "session_id": call.session_id,
                    "available": False,
                    "markdown": "",
                    "structured": {},
                },
            )

        available = bool(structured)
        return tool_ok(
            call=call,
            payload={
                "scope": self.memory_scope.value,
                "session_id": call.session_id,
                "available": available,
                "markdown": markdown if available else "",
                "structured": structured if available else {},
            },
        )
