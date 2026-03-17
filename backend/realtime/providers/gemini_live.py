from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from websockets import exceptions as ws_exceptions

from backend.core.settings import Settings
from backend.realtime.bridge import IOSRealtimeBridge
from backend.realtime.client import (
    INPUT_AUDIO_SAMPLE_RATE,
    RealtimeClientError,
    RealtimeClosedError,
    RealtimeConnectionError,
    RealtimeProtocolError,
    RealtimeReceiveError,
    RealtimeSendError,
)
from backend.realtime.contracts import (
    NormalizedRealtimeEvent,
    NormalizedRealtimeEventTypes,
    RealtimeProviderCapabilities,
)
from backend.realtime.factory import (
    BinarySender,
    BridgeBinding,
    BridgeBindingContext,
    ControlSender,
)
from backend.tools.contracts import ToolDefinition
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.protocol.contracts import now_ms

logger = logging.getLogger(__name__)

_GEMINI_DEFAULT_BASE_URL = "wss://generativelanguage.googleapis.com"
_GEMINI_DEFAULT_ENDPOINT = "/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
_GEMINI_AUDIO_MIME_TYPE = f"audio/pcm;rate={INPUT_AUDIO_SAMPLE_RATE}"

GEMINI_LIVE_REALTIME_CAPABILITIES = RealtimeProviderCapabilities(
    streaming_audio_input=True,
    streaming_audio_output=True,
    server_vad=False,
    manual_turn_commit_required=True,
    tool_calling=True,
    tool_result_submission_mode="provider_call_id",
    voice_selection=False,
    interruption_cancel=False,
    startup_validation=True,
)


def validate_gemini_live_realtime_settings(settings: Settings) -> None:
    settings.require_realtime_api_key(provider="gemini_live")

    model = settings.resolve_realtime_model(provider="gemini_live").strip()
    if not model:
        raise RuntimeError(
            "GEMINI_LIVE_MODEL is required when REALTIME_PROVIDER=gemini_live"
        )

    base_url = settings.resolve_realtime_base_url(provider="gemini_live")
    if base_url:
        normalized = base_url.strip().lower()
        if not (
            normalized.startswith("wss://")
            or normalized.startswith("ws://")
            or normalized.startswith("https://")
            or normalized.startswith("http://")
        ):
            raise RuntimeError(
                "GEMINI_LIVE_BASE_URL must start with ws://, wss://, http://, or https://"
            )

    endpoint = settings.resolve_realtime_endpoint(provider="gemini_live")
    if endpoint and not endpoint.strip().startswith("/"):
        raise RuntimeError("GEMINI_LIVE_ENDPOINT must start with '/'")


def build_gemini_live_session_bridge(
    *,
    settings: Settings,
    session_id: str,
    send_control: ControlSender,
    send_server_audio: BinarySender,
    realtime_tooling_runtime: RealtimeToolingRuntime | None = None,
    session_instructions: str | None = None,
    auto_start_response: bool = False,
) -> BridgeBinding:
    context = BridgeBindingContext()
    api_key = settings.require_realtime_api_key(provider="gemini_live")
    if not settings.openai_realtime_enable_manual_turn_fallback:
        logger.warning(
            "Gemini Live requires manual turn finalization; ignoring openai_realtime_enable_manual_turn_fallback=false"
        )
    base_instructions = settings.openai_realtime_instructions
    if isinstance(session_instructions, str) and session_instructions.strip():
        base_instructions = session_instructions.strip()
    effective_instructions = base_instructions
    if realtime_tooling_runtime is not None:
        effective_instructions = realtime_tooling_runtime.build_session_instructions(
            base_instructions=base_instructions,
        )

    client = GeminiLiveRealtimeClient(
        api_key=api_key,
        model=settings.resolve_realtime_model(provider="gemini_live"),
        instructions=effective_instructions,
        base_url=settings.resolve_realtime_base_url(provider="gemini_live"),
        endpoint=settings.resolve_realtime_endpoint(provider="gemini_live"),
        trace_events=settings.backend_debug_trace_ws_messages,
    )

    bridge = IOSRealtimeBridge(
        session_id=session_id,
        upstream_client=client,
        send_envelope=lambda message_type, payload: send_control(
            message_type,
            payload,
            target=context.record,
            fallback_session_id=session_id,
        ),
        send_binary_frame=send_server_audio,
        server_turn_detection_enabled=False,
        manual_turn_fallback_enabled=True,
        manual_turn_fallback_delay_ms=settings.openai_realtime_manual_turn_fallback_delay_ms,
        tooling_runtime=realtime_tooling_runtime,
        session_instructions=effective_instructions,
        auto_start_response=auto_start_response,
        response_create_starts_turn=False,
    )
    return BridgeBinding(bridge=bridge, context=context)


class GeminiLiveRealtimeClient:
    """Native websocket adapter for Gemini Live API.

    Protocol assumptions are intentionally explicit and conservative:
    - setup is sent through a `setup` envelope once per session init
    - audio uplink uses `realtimeInput.audio`
    - turn commit sends `realtimeInput.audioStreamEnd`
    - tool results use `toolResponse.functionResponses`
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        instructions: str,
        base_url: str | None = None,
        endpoint: str | None = None,
        trace_events: bool = False,
    ) -> None:
        resolved_api_key = api_key.strip()
        if not resolved_api_key:
            raise ValueError("api_key must be non-empty")

        resolved_model = model.strip()
        if not resolved_model:
            raise ValueError("model must be non-empty")

        self._api_key = resolved_api_key
        self._model = resolved_model
        self._instructions = instructions
        self._trace_events = trace_events

        self._base_url = self._normalize_base_url(base_url)
        self._endpoint = self._normalize_endpoint(endpoint)

        self._ws: Any | None = None
        self._registered_tools: tuple[ToolDefinition, ...] = ()
        self._session_initialized = False
        self._input_audio_append_count = 0
        self._output_audio_delta_count = 0
        self._response_sequence = 0
        self._active_response_id: str | None = None
        self._tool_call_names_by_id: dict[str, str] = {}
        self._pending_tool_call_ids: set[str] = set()
        self._cancelled_tool_call_ids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        ws = self._ws
        if ws is None:
            return False
        return not getattr(ws, "closed", False)

    @property
    def websocket_url(self) -> str:
        query = urlencode({"key": self._api_key})
        return f"{self._base_url}{self._endpoint}?{query}"

    @property
    def redacted_websocket_url(self) -> str:
        return self._redact_sensitive_url(self.websocket_url)

    async def connect(self) -> None:
        if self.is_connected:
            return

        try:
            self._ws = await websockets.connect(self.websocket_url)
        except Exception as exc:
            safe_endpoint = self.redacted_websocket_url
            safe_detail = self._redact_sensitive_text(str(exc))
            logger.warning(
                "Gemini Live websocket connect failed type=%s detail=%s endpoint=%s",
                type(exc).__name__,
                safe_detail,
                safe_endpoint,
            )
            raise RealtimeConnectionError(
                f"Failed to connect to realtime endpoint: {safe_endpoint}"
            ) from exc

        if self._trace_events:
            logger.info(
                "Gemini Live websocket connected endpoint=%s model=%s",
                self.redacted_websocket_url,
                self._model,
            )

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        self._session_initialized = False
        self._active_response_id = None
        if ws is None:
            return

        try:
            await ws.close()
        except Exception as exc:
            raise RealtimeClientError("Failed to close websocket cleanly") from exc

    async def initialize_session(
        self,
        *,
        instructions: str | None = None,
        voice: str | None = None,
        tools: Sequence[ToolDefinition] | None = None,
    ) -> None:
        if voice:
            logger.warning(
                "Gemini Live voice override is not currently mapped in adapter; ignoring voice=%s",
                voice,
            )

        self._registered_tools = tuple(tools or ())
        await self.send_json(
            self._build_setup_event(
                instructions=instructions,
                tools=self._registered_tools,
            )
        )
        self._session_initialized = True

    async def update_session(self, payload: dict[str, Any]) -> None:
        logger.warning(
            "Gemini Live session.update is not supported by adapter; ignoring payload keys=%s",
            sorted(payload.keys()),
        )

    async def append_client_audio(self, pcm16_audio: bytes) -> None:
        if not pcm16_audio:
            return
        await self.send_json(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": _GEMINI_AUDIO_MIME_TYPE,
                        "data": base64.b64encode(pcm16_audio).decode("ascii"),
                    }
                }
            }
        )

    async def commit_client_turn(self) -> None:
        await self.send_json(
            {
                "realtimeInput": {
                    "audioStreamEnd": True,
                }
            }
        )

    async def create_response(self) -> None:
        # Gemini Live generation starts from realtime input turn boundaries.
        logger.debug("Gemini Live response.create treated as no-op")

    async def cancel_response(self, *, response_id: str | None = None) -> None:
        logger.warning(
            "Gemini Live response.cancel is not mapped by adapter; ignoring response_id=%s",
            response_id,
        )

    async def register_tools(self, tools: Sequence[ToolDefinition]) -> None:
        self._registered_tools = tuple(tools)
        if self._session_initialized:
            logger.warning(
                "Gemini Live dynamic tool registration requires session restart; keeping new tool registry in-memory for next initialize_session"
            )

    async def submit_tool_result(
        self,
        *,
        call_id: str,
        output: str,
    ) -> None:
        if call_id not in self._pending_tool_call_ids:
            logger.info(
                "Ignoring Gemini toolResponse for non-pending or cancelled call session_call_id=%s",
                call_id,
            )
            return
        self._pending_tool_call_ids.discard(call_id)
        self._cancelled_tool_call_ids.discard(call_id)
        tool_name = self._tool_call_names_by_id.pop(call_id, "tool")
        parsed_output = self._decode_tool_output(output)
        await self.send_json(
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": call_id,
                            "name": tool_name,
                            "response": parsed_output,
                        }
                    ]
                }
            }
        )

    async def maybe_recover_session_init_error(
        self,
        *,
        code: str,
        message: str,
        tools: Sequence[ToolDefinition] | None = None,
        instructions: str | None = None,
    ) -> bool:
        _ = (code, message, tools, instructions)
        return False

    async def send_json(self, event: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or getattr(ws, "closed", False):
            raise RealtimeClosedError("Websocket is not connected")

        try:
            payload = json.dumps(event)
        except (TypeError, ValueError) as exc:
            raise RealtimeProtocolError("Event is not JSON serializable") from exc

        try:
            await ws.send(payload)
        except ws_exceptions.ConnectionClosed as exc:
            raise RealtimeClosedError("Websocket is closed") from exc
        except Exception as exc:
            raise RealtimeSendError("Failed to send event") from exc

        if self._trace_events:
            realtime_input = event.get("realtimeInput")
            if isinstance(realtime_input, dict) and "audio" in realtime_input:
                self._input_audio_append_count += 1
                if self._input_audio_append_count == 1:
                    logger.debug("Gemini Live send type=realtimeInput.audio count=1")
            elif "setup" in event:
                logger.debug("Gemini Live send type=setup")
            elif "toolResponse" in event:
                logger.debug("Gemini Live send type=toolResponse")
            else:
                logger.debug("Gemini Live send keys=%s", sorted(event.keys()))

    async def recv_json(self) -> dict[str, Any]:
        ws = self._ws
        if ws is None or getattr(ws, "closed", False):
            raise RealtimeClosedError("Websocket is not connected")

        try:
            raw_message = await ws.recv()
        except ws_exceptions.ConnectionClosed as exc:
            raise RealtimeClosedError("Websocket is closed") from exc
        except Exception as exc:
            raise RealtimeReceiveError("Failed to receive event") from exc

        if isinstance(raw_message, bytes):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RealtimeProtocolError("Received non-UTF8 websocket frame") from exc

        if not isinstance(raw_message, str):
            raise RealtimeProtocolError("Received unsupported websocket message type")

        try:
            event = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise RealtimeProtocolError("Received invalid JSON from realtime API") from exc

        if not isinstance(event, dict):
            raise RealtimeProtocolError("Realtime event must be a JSON object")

        return event

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            try:
                yield await self.recv_json()
            except RealtimeClosedError:
                return

    async def iter_normalized_events(self) -> AsyncIterator[NormalizedRealtimeEvent]:
        async for event in self.iter_events():
            normalized_events = self._to_normalized_runtime_events(event)
            for normalized in normalized_events:
                if self._trace_events:
                    event_type = normalized.get("type")
                    if event_type == NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DELTA:
                        self._output_audio_delta_count += 1
                        if self._output_audio_delta_count == 1:
                            logger.debug("Gemini Live recv normalized=response.audio.delta count=1")
                    elif event_type == NormalizedRealtimeEventTypes.RESPONSE_DONE:
                        logger.debug("Gemini Live recv normalized=response.done")
                        self._output_audio_delta_count = 0
                    elif event_type == NormalizedRealtimeEventTypes.SESSION_READY:
                        logger.debug("Gemini Live recv normalized=session.ready")
                    elif event_type == NormalizedRealtimeEventTypes.UNHANDLED:
                        logger.debug("Gemini Live recv normalized=provider.event.unhandled")
                yield normalized

    def _to_normalized_runtime_events(
        self,
        event: dict[str, Any],
    ) -> list[NormalizedRealtimeEvent]:
        if "setupComplete" in event:
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.SESSION_READY,
                    source="setupComplete",
                    payload={"type": "setupComplete", **event},
                    raw=event,
                )
            ]

        if "error" in event:
            error_payload = event.get("error")
            if not isinstance(error_payload, dict):
                error_payload = {"message": "Unknown upstream error"}
            code = error_payload.get("code")
            message = error_payload.get("message")
            normalized_error = {
                "error": {
                    "code": str(code) if code is not None else "UPSTREAM_ERROR",
                    "message": (
                        str(message)
                        if message is not None
                        else "Unknown upstream error"
                    ),
                    "retriable": self._is_retriable_error(error_payload),
                }
            }
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.ERROR,
                    source="error",
                    payload=normalized_error,
                    raw=event,
                )
            ]

        if "toolCallCancellation" in event:
            return self._normalize_tool_call_cancellation_events(event)

        if "toolCall" in event:
            return self._normalize_tool_call_events(event)

        server_content = event.get("serverContent")
        if isinstance(server_content, dict):
            return self._normalize_server_content_events(event, server_content)

        return [
            self._normalized_event(
                normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                source="unhandled",
                payload=event,
                raw=event,
            )
        ]

    def _normalize_server_content_events(
        self,
        raw_event: dict[str, Any],
        server_content: dict[str, Any],
    ) -> list[NormalizedRealtimeEvent]:
        events: list[NormalizedRealtimeEvent] = []
        response_id = self._ensure_active_response_id()

        model_turn = server_content.get("modelTurn")
        parts = model_turn.get("parts") if isinstance(model_turn, dict) else None
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline_data = part.get("inlineData")
                if not isinstance(inline_data, dict):
                    continue
                data = inline_data.get("data")
                mime_type = inline_data.get("mimeType")
                if not isinstance(data, str) or not data:
                    continue
                if isinstance(mime_type, str) and not mime_type.startswith("audio/"):
                    continue
                events.append(
                    self._normalized_event(
                        normalized_type=NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DELTA,
                        source="serverContent.modelTurn.inlineData",
                        payload={
                            "type": "response.output_audio.delta",
                            "delta": data,
                            "response_id": response_id,
                        },
                        raw=raw_event,
                    )
                )

        generation_complete = bool(server_content.get("generationComplete"))
        turn_complete = bool(server_content.get("turnComplete"))
        interrupted = bool(server_content.get("interrupted"))

        if generation_complete or turn_complete or interrupted:
            events.append(
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.RESPONSE_DONE,
                    source="serverContent.turnComplete",
                    payload={
                        "type": "response.done",
                        "response": {"id": response_id},
                        "response_id": response_id,
                    },
                    raw=raw_event,
                )
            )
            self._active_response_id = None
            self._pending_tool_call_ids.clear()
            self._cancelled_tool_call_ids.clear()

        if not events:
            events.append(
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="serverContent",
                    payload=raw_event,
                    raw=raw_event,
                )
            )

        return events

    def _normalize_tool_call_events(
        self,
        raw_event: dict[str, Any],
    ) -> list[NormalizedRealtimeEvent]:
        tool_call = raw_event.get("toolCall")
        if not isinstance(tool_call, dict):
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="toolCall",
                    payload=raw_event,
                    raw=raw_event,
                )
            ]

        function_calls = tool_call.get("functionCalls")
        if not isinstance(function_calls, list) or not function_calls:
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="toolCall",
                    payload=raw_event,
                    raw=raw_event,
                )
            ]

        normalized_events: list[NormalizedRealtimeEvent] = []
        for function_call in function_calls:
            if not isinstance(function_call, dict):
                continue
            call_id = function_call.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"tool_call_{now_ms()}"
            if call_id in self._cancelled_tool_call_ids:
                self._cancelled_tool_call_ids.discard(call_id)
                self._pending_tool_call_ids.discard(call_id)
                self._tool_call_names_by_id.pop(call_id, None)
                continue
            tool_name = function_call.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"
            args = function_call.get("args")
            if args is None:
                args_payload: dict[str, Any] = {}
            elif isinstance(args, dict):
                args_payload = args
            else:
                args_payload = {"value": args}

            self._tool_call_names_by_id[call_id] = tool_name
            self._pending_tool_call_ids.add(call_id)
            normalized_events.append(
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.TOOL_CALL_COMPLETED,
                    source="toolCall.functionCalls",
                    payload={
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "id": call_id,
                            "call_id": call_id,
                            "name": tool_name,
                            "arguments": args_payload,
                        },
                        "call_id": call_id,
                        "name": tool_name,
                        "arguments": args_payload,
                    },
                    raw=raw_event,
                )
            )

        if normalized_events:
            return normalized_events

        return [
            self._normalized_event(
                normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                source="toolCall",
                payload=raw_event,
                raw=raw_event,
            )
        ]

    def _normalize_tool_call_cancellation_events(
        self,
        raw_event: dict[str, Any],
    ) -> list[NormalizedRealtimeEvent]:
        cancellation = raw_event.get("toolCallCancellation")
        if not isinstance(cancellation, dict):
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="toolCallCancellation",
                    payload=raw_event,
                    raw=raw_event,
                )
            ]

        raw_ids = cancellation.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="toolCallCancellation",
                    payload=raw_event,
                    raw=raw_event,
                )
            ]

        call_ids: list[str] = []
        for raw_call_id in raw_ids:
            if not isinstance(raw_call_id, str) or not raw_call_id:
                continue
            call_ids.append(raw_call_id)
            self._cancelled_tool_call_ids.add(raw_call_id)
            self._pending_tool_call_ids.discard(raw_call_id)
            self._tool_call_names_by_id.pop(raw_call_id, None)

        if not call_ids:
            return [
                self._normalized_event(
                    normalized_type=NormalizedRealtimeEventTypes.UNHANDLED,
                    source="toolCallCancellation",
                    payload=raw_event,
                    raw=raw_event,
                )
            ]

        return [
            self._normalized_event(
                normalized_type=NormalizedRealtimeEventTypes.TOOL_CALL_CANCELLED,
                source="toolCallCancellation",
                payload={
                    "type": "tool.call.cancelled",
                    "call_ids": call_ids,
                },
                raw=raw_event,
            )
        ]

    def _build_setup_event(
        self,
        *,
        instructions: str | None,
        tools: Sequence[ToolDefinition],
    ) -> dict[str, Any]:
        resolved_model = self._model
        if not resolved_model.startswith("models/"):
            resolved_model = f"models/{resolved_model}"

        setup: dict[str, Any] = {
            "model": resolved_model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
            },
        }

        resolved_instructions = self._instructions if instructions is None else instructions
        if isinstance(resolved_instructions, str) and resolved_instructions.strip():
            setup["systemInstruction"] = {
                "parts": [
                    {
                        "text": resolved_instructions.strip(),
                    }
                ]
            }

        if tools:
            setup["tools"] = [
                {
                    "functionDeclarations": [
                        self._to_gemini_function_declaration(tool) for tool in tools
                    ]
                }
            ]

        return {"setup": setup}

    @staticmethod
    def _to_gemini_function_declaration(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        }

    def _ensure_active_response_id(self) -> str:
        if self._active_response_id is not None:
            return self._active_response_id
        self._response_sequence += 1
        self._active_response_id = f"gemini_response_{self._response_sequence}"
        return self._active_response_id

    @staticmethod
    def _decode_tool_output(output: str) -> dict[str, Any]:
        stripped = output.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return {"output": output}
        if isinstance(parsed, dict):
            return parsed
        return {"output": parsed}

    @staticmethod
    def _is_retriable_error(error_payload: dict[str, Any]) -> bool:
        explicit = error_payload.get("retriable")
        explicit_bool = GeminiLiveRealtimeClient._parse_optional_bool(explicit)
        if explicit_bool is not None:
            return explicit_bool

        code = error_payload.get("code")
        status = error_payload.get("status")
        message = error_payload.get("message")
        normalized = " ".join(
            str(part).strip().lower()
            for part in (code, status, message)
            if part is not None and str(part).strip()
        )

        numeric_code: int | None = None
        if isinstance(code, int):
            numeric_code = code
        elif isinstance(code, str) and code.strip().isdigit():
            numeric_code = int(code.strip())

        if numeric_code is not None:
            if numeric_code in {408, 425, 429, 500, 502, 503, 504}:
                return True
            if 500 <= numeric_code <= 599:
                return True
            if 400 <= numeric_code <= 499:
                return False

        non_retriable_markers = (
            "invalid_argument",
            "failed_precondition",
            "unauthenticated",
            "permission_denied",
            "not_found",
            "already_exists",
            "out_of_range",
            "unimplemented",
            "unsupported",
            "forbidden",
            "authentication",
            "invalid api key",
            "api key not valid",
            "malformed",
        )
        if any(marker in normalized for marker in non_retriable_markers):
            return False

        retriable_markers = (
            "resource_exhausted",
            "unavailable",
            "deadline_exceeded",
            "internal",
            "aborted",
            "timeout",
            "temporar",
            "try again",
            "rate limit",
        )
        if any(marker in normalized for marker in retriable_markers):
            return True

        return False

    @staticmethod
    def _parse_optional_bool(raw: Any) -> bool | None:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"true", "1", "yes", "on", "t", "y"}:
                return True
            if normalized in {"false", "0", "no", "off", "f", "n"}:
                return False
        return None

    def _redact_sensitive_text(self, value: str) -> str:
        safe = value.replace(self.websocket_url, self.redacted_websocket_url)
        return re.sub(
            r"([?&](?:key|api_key|apikey|token|access_token)=)[^&\s]+",
            r"\1[REDACTED]",
            safe,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _redact_sensitive_url(url: str) -> str:
        try:
            parsed = urlsplit(url)
        except Exception:
            return re.sub(
                r"([?&](?:key|api_key|apikey|token|access_token)=)[^&\s]+",
                r"\1[REDACTED]",
                url,
                flags=re.IGNORECASE,
            )

        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        safe_query_pairs: list[tuple[str, str]] = []
        for key, value in query_pairs:
            if key.lower() in {"key", "api_key", "apikey", "token", "access_token"}:
                safe_query_pairs.append((key, "[REDACTED]"))
            else:
                safe_query_pairs.append((key, value))

        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(safe_query_pairs, doseq=True),
                parsed.fragment,
            )
        )

    @staticmethod
    def _normalize_base_url(raw: str | None) -> str:
        value = (raw or _GEMINI_DEFAULT_BASE_URL).strip().rstrip("/")
        if value.startswith("https://"):
            return "wss://" + value[len("https://") :]
        if value.startswith("http://"):
            return "ws://" + value[len("http://") :]
        return value

    @staticmethod
    def _normalize_endpoint(raw: str | None) -> str:
        value = (raw or _GEMINI_DEFAULT_ENDPOINT).strip()
        if not value.startswith("/"):
            return "/" + value
        return value

    @staticmethod
    def _normalized_event(
        *,
        normalized_type: str,
        source: str,
        payload: dict[str, Any],
        raw: Any,
    ) -> NormalizedRealtimeEvent:
        return {
            "type": normalized_type,
            "payload": payload,
            "source": source,
            "raw": raw,
        }


__all__ = [
    "GEMINI_LIVE_REALTIME_CAPABILITIES",
    "GeminiLiveRealtimeClient",
    "build_gemini_live_session_bridge",
    "validate_gemini_live_realtime_settings",
]
