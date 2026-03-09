from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    call_id: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    name: str
    call_id: str
    payload: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None

    def to_output_payload(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload.setdefault("ok", self.ok)
        if self.error_code is not None:
            payload.setdefault("error_code", self.error_code)
        if self.error_message is not None:
            payload.setdefault("error_message", self.error_message)
        return payload

    def to_output_json(self) -> str:
        return json.dumps(self.to_output_payload(), ensure_ascii=True, sort_keys=True)


class ToolExecutor(Protocol):
    async def __call__(self, call: ToolCall) -> ToolResult: ...
