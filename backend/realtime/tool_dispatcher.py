from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from backend.tools.contracts import ToolCall
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.protocol.contracts import now_ms

logger = logging.getLogger(__name__)


class UpstreamToolSender(Protocol):
    async def send_json(self, event: dict[str, Any]) -> None: ...


class ToolCallDispatcher:
    def __init__(
        self,
        *,
        session_id: str,
        upstream_client: UpstreamToolSender,
        tooling_runtime: RealtimeToolingRuntime | None,
        send_response_create: Callable[[str], Awaitable[None]],
        dedupe_limit: int = 512,
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._tooling_runtime = tooling_runtime
        self._send_response_create = send_response_create
        self._processed_tool_call_ids: dict[str, None] = {}
        self._processed_tool_item_ids: dict[str, None] = {}
        self._processed_tool_dedupe_limit = max(1, dedupe_limit)
        self._saw_duplicate_tool_call_event_for_turn = False

    @property
    def saw_duplicate_tool_call_event_for_turn(self) -> bool:
        return self._saw_duplicate_tool_call_event_for_turn

    def reset_turn_state(self) -> None:
        self._saw_duplicate_tool_call_event_for_turn = False

    async def handle_event(self, event: dict[str, Any]) -> None:
        if self._tooling_runtime is None:
            logger.warning(
                "Ignoring tool call event without tooling runtime session=%s type=%s",
                self._session_id,
                event.get("type"),
            )
            return

        call_id, item_id = self._extract_tool_call_dedupe_ids(event)
        duplicate, dedupe_key = self._is_duplicate_tool_call_event(
            call_id=call_id,
            item_id=item_id,
        )
        if duplicate:
            self._saw_duplicate_tool_call_event_for_turn = True
            logger.debug(
                "Ignoring duplicate tool call completion session=%s dedupe_key=%s event_type=%s",
                self._session_id,
                dedupe_key,
                event.get("type"),
            )
            return
        self._mark_tool_call_processed(call_id=call_id, item_id=item_id)

        tool_call_or_error = self._extract_tool_call_or_error(event)
        if isinstance(tool_call_or_error, dict):
            await self._send_tool_error_output(
                call_id=tool_call_or_error["call_id"],
                tool_name=tool_call_or_error["tool_name"],
                error_code=tool_call_or_error["error_code"],
                error_message=tool_call_or_error["error_message"],
            )
            return

        assert self._tooling_runtime is not None
        try:
            tool_result = await self._tooling_runtime.execute(tool_call_or_error)
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "Tool execution failed session=%s call_id=%s name=%s",
                self._session_id,
                tool_call_or_error.call_id,
                tool_call_or_error.name,
            )
            await self._send_tool_error_output(
                call_id=tool_call_or_error.call_id,
                tool_name=tool_call_or_error.name,
                error_code="TOOL_EXECUTION_FAILED",
                error_message=str(exc) or "Tool execution failed",
            )
            return

        await self._send_tool_output_and_continue(
            call_id=tool_result.call_id,
            output=tool_result.to_output_json(),
        )

    def _extract_tool_call_dedupe_ids(
        self,
        event: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        item = event.get("item")
        container = item if isinstance(item, dict) else event

        call_id = self._extract_non_empty_string(container, "call_id")
        if call_id is None:
            call_id = self._extract_non_empty_string(event, "call_id")
        item_id = self._extract_non_empty_string(container, "id")
        return call_id, item_id

    def _is_duplicate_tool_call_event(
        self,
        *,
        call_id: str | None,
        item_id: str | None,
    ) -> tuple[bool, str | None]:
        if call_id is not None and call_id in self._processed_tool_call_ids:
            return True, f"call_id:{call_id}"
        if item_id is not None and item_id in self._processed_tool_item_ids:
            return True, f"item_id:{item_id}"
        return False, None

    def _mark_tool_call_processed(
        self,
        *,
        call_id: str | None,
        item_id: str | None,
    ) -> None:
        if call_id is not None:
            self._remember_processed_tool_id(
                self._processed_tool_call_ids,
                call_id,
            )
        if item_id is not None:
            self._remember_processed_tool_id(
                self._processed_tool_item_ids,
                item_id,
            )

    def _remember_processed_tool_id(
        self,
        id_store: dict[str, None],
        processed_id: str,
    ) -> None:
        if processed_id in id_store:
            id_store.pop(processed_id, None)
        id_store[processed_id] = None
        while len(id_store) > self._processed_tool_dedupe_limit:
            oldest_id = next(iter(id_store))
            id_store.pop(oldest_id, None)

    def _extract_tool_call_or_error(
        self,
        event: dict[str, Any],
    ) -> ToolCall | dict[str, str]:
        item = event.get("item")
        container = item if isinstance(item, dict) else event

        tool_name = self._extract_non_empty_string(container, "name")
        call_id = self._extract_non_empty_string(container, "call_id")
        if call_id is None:
            call_id = self._extract_non_empty_string(event, "call_id")
        if tool_name is None:
            tool_name = self._extract_non_empty_string(event, "name")

        if call_id is None or tool_name is None:
            return {
                "call_id": call_id or f"tool_call_{now_ms()}",
                "tool_name": tool_name or "unknown_tool",
                "error_code": "INVALID_TOOL_CALL",
                "error_message": "Missing tool name or call_id in upstream function call event",
            }

        parsed_arguments = self._parse_tool_arguments(
            container.get("arguments", event.get("arguments"))
        )
        if isinstance(parsed_arguments, str):
            return {
                "call_id": call_id,
                "tool_name": tool_name,
                "error_code": "INVALID_TOOL_ARGUMENTS",
                "error_message": parsed_arguments,
            }

        return ToolCall(
            name=tool_name,
            call_id=call_id,
            session_id=self._session_id,
            arguments=parsed_arguments,
        )

    @staticmethod
    def _extract_non_empty_string(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any] | str:
        if raw_arguments is None:
            return {}
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            stripped = raw_arguments.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return "Tool arguments were not valid JSON"
            if not isinstance(parsed, dict):
                return "Tool arguments must decode to a JSON object"
            return parsed
        return "Tool arguments must be a JSON object or JSON string"

    async def _send_tool_error_output(
        self,
        *,
        call_id: str,
        tool_name: str,
        error_code: str,
        error_message: str,
    ) -> None:
        output_json = json.dumps(
            {
                "ok": False,
                "tool_name": tool_name,
                "session_id": self._session_id,
                "error_code": error_code,
                "error_message": error_message,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        await self._send_tool_output_and_continue(call_id=call_id, output=output_json)

    async def _send_tool_output_and_continue(self, *, call_id: str, output: str) -> None:
        await self._upstream_client.send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )
        await self._send_response_create("tool_output")
