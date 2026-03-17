from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from typing import Any

from backend.realtime.audio_uplink import ClientAudioUplink
from backend.realtime.client import RealtimeClientError
from backend.realtime.contracts import (
    BinarySender,
    EnvelopeSender,
    NormalizedRealtimeEvent,
    NormalizedRealtimeEventTypes,
    RealtimeAdapterContract,
)
from backend.realtime.tool_dispatcher import ToolCallDispatcher
from backend.realtime.turn_state import TurnConfig, TurnManager, TurnState
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.protocol.contracts import now_ms
from backend.ws.protocol.frame_codec import SERVER_AUDIO_FRAME_TYPE

logger = logging.getLogger(__name__)


class IOSRealtimeBridge:
    """Relay between iOS websocket transport and OpenAI realtime websocket."""

    def __init__(
        self,
        *,
        session_id: str,
        upstream_client: RealtimeAdapterContract,
        send_envelope: EnvelopeSender,
        send_binary_frame: BinarySender,
        server_turn_detection_enabled: bool = False,
        manual_turn_fallback_enabled: bool = True,
        manual_turn_fallback_delay_ms: int = 900,
        client_audio_queue_maxsize: int = 32,
        tooling_runtime: RealtimeToolingRuntime | None = None,
        session_instructions: str | None = None,
        auto_start_response: bool = False,
        response_create_starts_turn: bool = True,
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._send_envelope = send_envelope
        self._send_binary_frame = send_binary_frame
        self._tooling_runtime = tooling_runtime
        self._session_instructions = session_instructions
        self._auto_start_response = auto_start_response
        self._response_create_starts_turn = response_create_starts_turn

        self._upstream_task: asyncio.Task[None] | None = None
        self._tool_dispatch_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._session_ready_confirmed = False
        self._session_ready_event = asyncio.Event()
        self._session_ready_error: tuple[str, str] | None = None

        self._audio_uplink = ClientAudioUplink(
            session_id=session_id,
            upstream_client=upstream_client,
            queue_maxsize=client_audio_queue_maxsize,
        )
        self._tool_dispatcher = ToolCallDispatcher(
            session_id=session_id,
            upstream_client=upstream_client,
            tooling_runtime=tooling_runtime,
            send_response_create=self._send_response_create,
            send_onboarding_profile_ready=self._send_onboarding_profile_ready,
        )

        self._cancelled_response_ids: set[str] = set()
        self._started_response_ids: set[str] = set()
        self._last_stopped_response_id: str | None = None

        turn_config = TurnConfig(
            server_turn_detection_enabled=server_turn_detection_enabled,
            manual_turn_fallback_enabled=manual_turn_fallback_enabled,
            manual_turn_fallback_delay_s=max(100, manual_turn_fallback_delay_ms) / 1000.0,
        )
        turn_state = TurnState()
        self._turn_manager = TurnManager(
            session_id=session_id,
            config=turn_config,
            state=turn_state,
            upstream_client=upstream_client,
            audio_uplink=self._audio_uplink,
            tool_dispatcher=self._tool_dispatcher,
            send_envelope=send_envelope,
            response_create_starts_turn=response_create_starts_turn,
        )

    async def connect_and_start(self) -> None:
        self._session_ready_confirmed = False
        self._session_ready_error = None
        self._session_ready_event.clear()

        logger.info("Connecting upstream realtime session=%s", self._session_id)
        await self._upstream_client.connect()
        self._audio_uplink.start()
        self._upstream_task = asyncio.create_task(
            self._run_upstream_loop(),
            name=f"upstream_loop:{self._session_id}",
        )

        logger.info("Initializing upstream realtime session=%s", self._session_id)
        tools = None
        if self._tooling_runtime is not None:
            tools = self._tooling_runtime.list_tool_definitions()
        await self._upstream_client.initialize_session(
            tools=tools,
            instructions=self._session_instructions,
        )

        logger.info("Waiting for upstream session readiness session=%s", self._session_id)
        await self._wait_for_upstream_session_ready()
        logger.info("Upstream session ready session=%s", self._session_id)
        if self._auto_start_response:
            await self._send_response_create("session_auto_start")

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            logger.debug("Ignoring empty client audio payload for session=%s", self._session_id)
            return

        self._audio_uplink.enqueue(payload_bytes)
        self._turn_manager.on_client_audio()

    async def finalize_turn(self, *, reason: str = "client_end_turn") -> None:
        await self._turn_manager.finalize_turn_if_needed(reason=reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        self._turn_manager.cancel_all_tasks()

        tool_dispatch_tasks = list(self._tool_dispatch_tasks)
        self._tool_dispatch_tasks.clear()
        for task in tool_dispatch_tasks:
            task.cancel()
        for task in tool_dispatch_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        task = self._upstream_task
        self._upstream_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await self._audio_uplink.shutdown()
        with contextlib.suppress(RealtimeClientError):
            await self._upstream_client.close()

    async def _interrupt_active_response(self, *, reason: str) -> None:
        response_id = self._turn_manager.state.current_response_id
        if response_id is None or response_id in self._cancelled_response_ids:
            return

        logger.warning(
            "Interrupting assistant response session=%s response_id=%s reason=%s",
            self._session_id,
            response_id,
            reason,
        )

        self._cancelled_response_ids.add(response_id)
        self._started_response_ids.discard(response_id)
        self._last_stopped_response_id = response_id
        self._turn_manager.state.current_response_id = None

        try:
            await self._upstream_client.cancel_response(response_id=response_id)
            logger.info(
                "Upstream response.cancel sent session=%s response_id=%s",
                self._session_id,
                response_id,
            )
        except Exception:
            logger.exception(
                "Failed to send upstream response.cancel session=%s response_id=%s",
                self._session_id,
                response_id,
            )
        await self._send_envelope(
            "assistant.playback.control",
            {"command": "cancel_response", "response_id": response_id},
        )

    async def _run_upstream_loop(self) -> None:
        try:
            async for event in self._upstream_client.iter_normalized_events():
                await self._handle_upstream_event(event)
        except asyncio.CancelledError:
            raise
        except RealtimeClientError as exc:
            logger.warning("Upstream loop closed for %s: %s", self._session_id, exc)
            self._mark_session_init_failed(
                code="UPSTREAM_CONNECTION_ERROR",
                message="Upstream realtime connection failed",
            )
            await self._send_upstream_error(
                code="UPSTREAM_CONNECTION_ERROR",
                message="Upstream realtime connection failed",
                retriable=True,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.exception("Unexpected upstream loop failure for %s", self._session_id)
            self._mark_session_init_failed(
                code="UPSTREAM_UNEXPECTED_ERROR",
                message=str(exc),
            )
            await self._send_upstream_error(
                code="UPSTREAM_UNEXPECTED_ERROR",
                message=str(exc),
                retriable=True,
            )

    async def _handle_upstream_event(self, event: NormalizedRealtimeEvent) -> None:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            logger.debug("Ignoring upstream event with non-string type: %s", event)
            return

        handler = self._UPSTREAM_EVENT_HANDLERS.get(event_type)
        if handler is not None:
            await handler(self, event)
            return

        source = event.get("source")
        logger.debug("Unhandled upstream event type=%s source=%s", event_type, source)

    async def _handle_audio_delta(self, event: NormalizedRealtimeEvent) -> None:
        self._turn_manager.on_audio_delta()
        await self._on_audio_delta(event)

    async def _handle_audio_done(self, event: NormalizedRealtimeEvent) -> None:
        await self._on_response_done(event)

    async def _handle_response_done(self, event: NormalizedRealtimeEvent) -> None:
        await self._on_response_done(event)
        self._turn_manager.reset(self._tool_dispatcher)

    async def _handle_speech_started(self, event: NormalizedRealtimeEvent) -> None:
        self._turn_manager.on_vad_speech_started()
        logger.info("Upstream VAD speech_started session=%s", self._session_id)
        if self._turn_manager.state.current_response_id is not None:
            await self._interrupt_active_response(reason="speech_started")
        await self._send_envelope("assistant.thinking", {"status": "thinking"})

    async def _handle_speech_stopped(self, event: NormalizedRealtimeEvent) -> None:
        self._turn_manager.on_vad_speech_stopped()
        logger.info("Upstream VAD speech_stopped session=%s", self._session_id)
        await self._turn_manager.finalize_turn_if_needed(reason="speech_stopped")

    async def _handle_response_created(self, event: NormalizedRealtimeEvent) -> None:
        logger.info("Upstream response.created session=%s", self._session_id)
        self._turn_manager.on_response_created()

    async def _handle_function_call_done(self, event: NormalizedRealtimeEvent) -> None:
        raw_event = self._payload_dict(event)
        task = asyncio.create_task(
            self._tool_dispatcher.handle_event(raw_event),
            name=f"tool_dispatch:{self._session_id}",
        )
        self._tool_dispatch_tasks.add(task)
        task.add_done_callback(self._on_tool_dispatch_task_done)

    async def _handle_function_call_cancelled(self, event: NormalizedRealtimeEvent) -> None:
        payload = self._payload_dict(event)
        raw_call_ids = payload.get("call_ids")
        if not isinstance(raw_call_ids, list):
            return
        call_ids = [call_id for call_id in raw_call_ids if isinstance(call_id, str) and call_id]
        if not call_ids:
            return
        self._tool_dispatcher.cancel_pending_tool_calls(
            call_ids=call_ids,
            source=str(event.get("source") or "tool.call.cancelled"),
        )

    async def _handle_error_event(self, event: NormalizedRealtimeEvent) -> None:
        await self._on_upstream_error_event(event)

    def _on_tool_dispatch_task_done(self, task: asyncio.Task[None]) -> None:
        self._tool_dispatch_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "Tool dispatch task failed session=%s",
                self._session_id,
                exc_info=exc,
            )

    async def _handle_session_ready(self, event: NormalizedRealtimeEvent) -> None:
        source = event.get("source", "session.ready")
        logger.info("Upstream %s session=%s", source, self._session_id)
        self._mark_session_ready()

    async def _handle_input_audio_committed(self, event: NormalizedRealtimeEvent) -> None:
        logger.debug("Upstream input_audio_buffer.committed session=%s", self._session_id)

    _UPSTREAM_EVENT_HANDLERS: dict[str, Any] = {
        NormalizedRealtimeEventTypes.SESSION_READY: _handle_session_ready,
        NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DELTA: _handle_audio_delta,
        NormalizedRealtimeEventTypes.RESPONSE_AUDIO_DONE: _handle_audio_done,
        NormalizedRealtimeEventTypes.RESPONSE_DONE: _handle_response_done,
        NormalizedRealtimeEventTypes.INPUT_SPEECH_STARTED: _handle_speech_started,
        NormalizedRealtimeEventTypes.INPUT_SPEECH_STOPPED: _handle_speech_stopped,
        NormalizedRealtimeEventTypes.RESPONSE_CREATED: _handle_response_created,
        NormalizedRealtimeEventTypes.TOOL_CALL_COMPLETED: _handle_function_call_done,
        NormalizedRealtimeEventTypes.TOOL_CALL_CANCELLED: _handle_function_call_cancelled,
        NormalizedRealtimeEventTypes.INPUT_AUDIO_COMMITTED: _handle_input_audio_committed,
        NormalizedRealtimeEventTypes.ERROR: _handle_error_event,
    }

    async def _on_response_done(self, event: NormalizedRealtimeEvent) -> None:
        response_id = self._extract_response_id(event)

        if response_id is None and len(self._started_response_ids) == 1:
            response_id = next(iter(self._started_response_ids))

        if response_id is None:
            response_id = self._turn_manager.state.current_response_id

        if response_id is None:
            return

        if response_id == self._last_stopped_response_id:
            return

        self._started_response_ids.discard(response_id)
        if response_id in self._cancelled_response_ids:
            self._cancelled_response_ids.remove(response_id)
            return

        self._last_stopped_response_id = response_id
        await self._send_envelope(
            "assistant.playback.control",
            {"command": "stop_response", "response_id": response_id},
        )

    async def _on_audio_delta(self, event: NormalizedRealtimeEvent) -> None:
        payload = self._payload_dict(event)
        delta_b64 = payload.get("delta")
        if not isinstance(delta_b64, str) or not delta_b64:
            return

        response_id = self._resolve_response_id(event)
        if response_id in self._cancelled_response_ids:
            logger.info(
                "Ignoring late audio delta for cancelled response session=%s response_id=%s",
                self._session_id,
                response_id,
            )
            return
        if response_id not in self._started_response_ids:
            self._started_response_ids.add(response_id)
            if self._last_stopped_response_id == response_id:
                self._last_stopped_response_id = None
            await self._send_envelope(
                "assistant.playback.control",
                {"command": "start_response", "response_id": response_id},
            )

        self._turn_manager.state.current_response_id = response_id
        try:
            pcm_bytes = base64.b64decode(delta_b64, validate=True)
        except (ValueError, TypeError):
            logger.warning("Invalid base64 audio delta for session=%s", self._session_id)
            return
        await self._send_binary_frame(SERVER_AUDIO_FRAME_TYPE, now_ms(), pcm_bytes)

    def _resolve_response_id(self, event: NormalizedRealtimeEvent) -> str:
        resolved = self._extract_response_id(event)
        if resolved is not None:
            return resolved
        if self._turn_manager.state.current_response_id is not None:
            return self._turn_manager.state.current_response_id
        fallback = f"response_{now_ms()}"
        self._turn_manager.state.current_response_id = fallback
        return fallback

    @staticmethod
    def _extract_response_id(event: NormalizedRealtimeEvent) -> str | None:
        payload = IOSRealtimeBridge._payload_dict(event)
        direct = payload.get("response_id")
        if isinstance(direct, str) and direct:
            return direct
        response = payload.get("response")
        if isinstance(response, dict):
            rid = response.get("id")
            if isinstance(rid, str) and rid:
                return rid
        return None

    async def _on_upstream_error_event(self, event: NormalizedRealtimeEvent) -> None:
        payload = self._payload_dict(event)
        err_payload = payload.get("error")
        if not isinstance(err_payload, dict):
            await self._send_upstream_error(
                code="UPSTREAM_ERROR",
                message="Unknown upstream error",
                retriable=True,
            )
            return

        code = err_payload.get("code")
        message = err_payload.get("message")
        if not isinstance(code, str) or not code:
            code = "UPSTREAM_ERROR"
        if not isinstance(message, str) or not message:
            message = "Unknown upstream error"

        if self._turn_manager.is_interrupt_race_expected(
            code=code,
            message=message,
            saw_duplicate_tool_call=self._tool_dispatcher.saw_duplicate_tool_call_event_for_turn,
        ):
            logger.info(
                "Ignoring expected interrupt race error session=%s code=%s message=%s",
                self._session_id,
                code,
                message,
            )
            return

        if await self._maybe_retry_legacy_session_init(code=code, message=message):
            logger.info(
                "Realtime session init schema mismatch recovered via legacy retry session=%s code=%s message=%s",
                self._session_id,
                code,
                message,
            )
            return

        lower_code = code.lower()
        lower_message = message.lower()
        if (
            ("unknown_parameter" in lower_code or "invalid_parameter" in lower_code)
            and "turn_detection" in lower_message
        ) or ("unknown parameter" in lower_message and "turn_detection" in lower_message):
            logger.error(
                "REALTIME_SESSION_INIT_SCHEMA_ERROR session=%s code=%s message=%s",
                self._session_id,
                code,
                message,
            )

        retriable = self._parse_retriable_flag(err_payload.get("retriable", True))
        self._mark_session_init_failed(code=code, message=message)
        await self._send_upstream_error(code=code, message=message, retriable=retriable)

    async def _wait_for_upstream_session_ready(self, timeout_seconds: float = 8.0) -> None:
        try:
            await asyncio.wait_for(self._session_ready_event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise RealtimeClientError(
                "Timed out waiting for upstream session readiness confirmation"
            ) from exc

        if self._session_ready_confirmed:
            return

        code, message = self._session_ready_error or (
            "UPSTREAM_SESSION_NOT_READY",
            "Upstream session was not confirmed as ready",
        )
        raise RealtimeClientError(f"{code}: {message}")

    def _mark_session_ready(self) -> None:
        if self._session_ready_confirmed:
            return
        self._session_ready_confirmed = True
        self._session_ready_error = None
        self._session_ready_event.set()

    def _mark_session_init_failed(self, *, code: str, message: str) -> None:
        if self._session_ready_confirmed:
            return
        self._session_ready_error = (code, message)
        self._session_ready_event.set()

    async def _maybe_retry_legacy_session_init(self, *, code: str, message: str) -> bool:
        try:
            tools = None
            if self._tooling_runtime is not None:
                tools = self._tooling_runtime.list_tool_definitions()
            did_retry = await self._upstream_client.maybe_recover_session_init_error(
                code=code,
                message=message,
                tools=tools,
                instructions=self._session_instructions,
            )
        except RealtimeClientError:
            return False
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception("Unexpected failure retrying legacy session init schema fallback")
            return False
        return bool(did_retry)

    def client_end_turn_ignore_reason(self) -> str | None:
        return self._turn_manager.client_end_turn_ignore_reason()

    async def _send_upstream_error(
        self,
        *,
        code: str,
        message: str,
        retriable: bool,
    ) -> None:
        await self._send_envelope(
            "error",
            {
                "code": code,
                "message": message,
                "retriable": retriable,
            },
        )

    async def _send_response_create(self, source: str) -> None:
        await self._upstream_client.create_response()
        if self._response_create_starts_turn:
            self._turn_manager.on_response_created()
        logger.info(
            "Upstream response.create sent session=%s source=%s starts_turn=%s",
            self._session_id,
            source,
            self._response_create_starts_turn,
        )

    async def _send_onboarding_profile_ready(self, payload: dict[str, Any]) -> None:
        missing_required_fields = payload.get("missing_required_fields")
        if not isinstance(missing_required_fields, list):
            missing_required_fields = []
        await self._send_envelope(
            "onboarding.profile_ready",
            {
                "ready": True,
                "missing_required_fields": missing_required_fields,
            },
        )

    @staticmethod
    def _parse_retriable_flag(raw: Any) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"false", "0", "no", "off", "f", "n"}:
                return False
            if normalized in {"true", "1", "yes", "on", "t", "y"}:
                return True
        return True

    @staticmethod
    def _payload_dict(event: NormalizedRealtimeEvent) -> dict[str, Any]:
        payload = event.get("payload")
        if isinstance(payload, dict):
            return payload
        return {}
