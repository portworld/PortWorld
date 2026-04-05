from __future__ import annotations

from typing import Any

from backend.tools.contracts import ToolCall, ToolResult


def tool_ok(*, call: ToolCall, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        ok=True,
        name=call.name,
        call_id=call.call_id,
        payload=payload,
    )


def tool_error(
    *,
    call: ToolCall,
    error_code: str,
    error_message: str,
    payload: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        name=call.name,
        call_id=call.call_id,
        payload={} if payload is None else payload,
        error_code=error_code,
        error_message=error_message,
    )
