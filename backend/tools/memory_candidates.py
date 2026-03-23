from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from backend.core.storage import BackendStorage
from backend.memory.candidates import build_memory_candidate
from backend.tools.contracts import ToolCall, ToolResult
from backend.tools.results import tool_error, tool_ok

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryCandidateToolExecutor:
    storage: BackendStorage

    async def __call__(self, call: ToolCall) -> ToolResult:
        candidate = build_memory_candidate(
            session_id=call.session_id,
            scope=call.arguments.get("scope"),
            section_hint=call.arguments.get("section_hint"),
            fact=call.arguments.get("fact"),
            stability=call.arguments.get("stability"),
            confidence=call.arguments.get("confidence"),
        )
        if candidate is None:
            return tool_error(
                call=call,
                error_code="INVALID_MEMORY_CANDIDATE",
                error_message="Memory candidate payload is invalid",
                payload={"session_id": call.session_id},
            )

        try:
            await asyncio.to_thread(
                self.storage.append_memory_candidate,
                session_id=call.session_id,
                candidate=dict(candidate),
            )
        except OSError as exc:
            logger.warning(
                "Memory candidate write failed session_id=%s call_id=%s",
                call.session_id,
                call.call_id,
                exc_info=exc,
            )
            return tool_error(
                call=call,
                error_code="MEMORY_CANDIDATE_WRITE_FAILED",
                error_message="Could not persist memory candidate",
                payload={"session_id": call.session_id},
            )

        return tool_ok(
            call=call,
            payload={
                "session_id": call.session_id,
                "captured": True,
                "candidate": dict(candidate),
            },
        )
