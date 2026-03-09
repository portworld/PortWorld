from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.tools.contracts import ToolCall, ToolDefinition, ToolExecutor, ToolResult


class ToolRegistryError(Exception):
    """Base error for realtime tool registry failures."""


class DuplicateToolError(ToolRegistryError):
    """Raised when attempting to register the same tool name twice."""


class UnknownToolError(ToolRegistryError):
    """Raised when resolving an unknown tool name."""


class ToolNotImplementedError(ToolRegistryError):
    """Raised when a declared tool exists but has no real executor yet."""


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    executor: ToolExecutor


class RealtimeToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, *, definition: ToolDefinition, executor: ToolExecutor) -> None:
        if definition.name in self._tools:
            raise DuplicateToolError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = RegisteredTool(
            definition=definition,
            executor=executor,
        )

    def resolve(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"Unknown tool: {name}") from exc

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def list_definitions(self) -> list[ToolDefinition]:
        return [registered.definition for registered in self._tools.values()]

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [definition.to_openai_tool() for definition in self.list_definitions()]

    async def execute(self, call: ToolCall) -> ToolResult:
        registered = self.resolve(call.name)
        return await registered.executor(call)


class NotImplementedToolExecutor:
    def __init__(self, *, tool_name: str) -> None:
        self._tool_name = tool_name

    async def __call__(self, call: ToolCall) -> ToolResult:
        raise ToolNotImplementedError(f"Tool not implemented yet: {self._tool_name}")
