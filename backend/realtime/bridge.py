from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from typing import Any

from backend.realtime.audio_uplink import ClientAudioUplink
from backend.realtime.client import OpenAIRealtimeClient, RealtimeClientError
from backend.realtime.contracts import BinarySender, EnvelopeSender
from backend.realtime.tool_dispatcher import ToolCallDispatcher
from backend.tools.runtime import RealtimeToolingRuntime
from backend.ws.protocol.contracts import now_ms
from backend.ws.protocol.frame_codec import SERVER_AUDIO_FRAME_TYPE

logger = logging.getLogger(__name__)
SESSION_READY_EVENT_TYPES = {"session.created", "session.updated"}


class IOSRealtimeBridge:
    """Relay between iOS websocket transport and OpenAI realtime websocket."""

    def __init__(
        self,
        *,
        session_id: str,
        upstream_client: OpenAIRealtimeClient,
        send_envelope: EnvelopeSender,
        send_binary_frame: BinarySender,
        server_turn_detection_enabled: bool = False,
        manual_turn_fallback_enabled: bool = True,
        manual_turn_fallback_delay_ms: int = 900,
        client_audio_queue_maxsize: int = 32,
        tooling_runtime: RealtimeToolingRuntime | None = None,
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._send_envelope = send_envelope
        self._send_binary_frame = send_binary_frame
        self._tooling_runtime = tooling_runtime

        self._upstream_task: asyncio.Task[None] | None = None
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
        )

        self._cancelled_response_ids: set[str] = set()
        self._started_response_ids: set[str] = set()
        self._last_stopped_response_id: str | None = None
        self._current_response_id: str | None = None
        self._has_active_upstream_response = False
        self._server_turn_detection_enabled = server_turn_detection_enabled
        self._manual_turn_fallback_enabled = manual_turn_fallback_enabled
        self._manual_turn_fallback_delay_s = max(100, manual_turn_fallback_delay_ms) / 1000.0
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._manual_finalize_error_sent_for_turn = False
        self._last_client_audio_at_monotonic: float | None = None
        self._server_vad_speaking = False
        self._manual_finalize_task: asyncio.Task[None] | None = None
        self._turn_finalize_lock = asyncio.Lock()

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
            tools = self._tooling_runtime.to_openai_tools()
        await self._upstream_client.initialize_session(tools=tools)

        logger.info("Waiting for upstream session readiness session=%s", self._session_id)
        await self._wait_for_upstream_session_ready()
        logger.info("Upstream session ready session=%s", self._session_id)

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            logger.debug("Ignoring empty client audio payload for session=%s", self._session_id)
            return

        self._audio_uplink.enqueue(payload_bytes)
        self._current_turn_audio_seen = True
        self._last_client_audio_at_monotonic = time.monotonic()
        self._schedule_inactivity_finalize()

    async def finalize_turn(self, *, reason: str = "client_end_turn") -> None:
        await self._finalize_turn_if_needed_locked(reason=reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        self._cancel_manual_finalize_task()

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
        response_id = self._current_response_id
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
        self._current_response_id = None

        try:
            await self._upstream_client.send_json(
                {"type": "response.cancel", "response_id": response_id}
            )
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
            async for event in self._upstream_client.iter_events():
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

    async def _handle_upstream_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            logger.debug("Ignoring upstream event with non-string type: %s", event)
            return

        if event_type == "response.output_audio.delta":
            self._current_turn_response_started = True
            await self._on_audio_delta(event)
            return

        if event_type == "response.output_audio.done":
            await self._on_response_done(event)
            return

        if event_type == "response.done":
            await self._on_response_done(event)
            self._reset_turn_state()
            return

        if event_type == "input_audio_buffer.speech_started":
            self._server_vad_speaking = True
            logger.info("Upstream VAD speech_started session=%s", self._session_id)
            if self._current_response_id is not None:
                await self._interrupt_active_response(reason="speech_started")
            await self._send_envelope("assistant.thinking", {"status": "thinking"})
            return

        if event_type == "input_audio_buffer.speech_stopped":
            self._server_vad_speaking = False
            logger.info("Upstream VAD speech_stopped session=%s", self._session_id)
            await self._finalize_turn_if_needed_locked(reason="speech_stopped")
            return

        if event_type == "response.created":
            logger.info("Upstream response.created session=%s", self._session_id)
            self._has_active_upstream_response = True
            self._current_turn_response_started = True
            self._cancel_manual_finalize_task()
            return

        if event_type == "response.function_call_arguments.done":
            await self._tool_dispatcher.handle_event(event)
            return

        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                await self._tool_dispatcher.handle_event(event)
                return

        if event_type in SESSION_READY_EVENT_TYPES:
            logger.info("Upstream %s session=%s", event_type, self._session_id)
            self._mark_session_ready()
            return

        if event_type == "input_audio_buffer.committed":
            logger.debug("Upstream input_audio_buffer.committed session=%s", self._session_id)
            return

        if event_type == "error":
            await self._on_upstream_error_event(event)
            return

        logger.debug("Unhandled upstream event type=%s", event_type)

    async def _on_response_done(self, event: dict[str, Any]) -> None:
        response_id = self._extract_response_id(event)

        if response_id is None and len(self._started_response_ids) == 1:
            response_id = next(iter(self._started_response_ids))

        if response_id is None:
            response_id = self._current_response_id

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

    async def _on_audio_delta(self, event: dict[str, Any]) -> None:
        delta_b64 = event.get("delta")
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

        self._current_response_id = response_id
        try:
            pcm_bytes = base64.b64decode(delta_b64, validate=True)
        except (ValueError, TypeError):
            logger.warning("Invalid base64 audio delta for session=%s", self._session_id)
            return
        await self._send_binary_frame(SERVER_AUDIO_FRAME_TYPE, now_ms(), pcm_bytes)

    def _resolve_response_id(self, event: dict[str, Any]) -> str:
        resolved = self._extract_response_id(event)
        if resolved is not None:
            return resolved
        if self._current_response_id is not None:
            return self._current_response_id
        fallback = f"response_{now_ms()}"
        self._current_response_id = fallback
        return fallback

    @staticmethod
    def _extract_response_id(event: dict[str, Any]) -> str | None:
        direct = event.get("response_id")
        if isinstance(direct, str) and direct:
            return direct
        response = event.get("response")
        if isinstance(response, dict):
            rid = response.get("id")
            if isinstance(rid, str) and rid:
                return rid
        return None

    async def _on_upstream_error_event(self, event: dict[str, Any]) -> None:
        err_payload = event.get("error")
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

        if self._is_expected_interrupt_race_error(code=code, message=message):
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
        if not self._is_session_init_schema_error(code=code, message=message):
            return False

        retry_method = getattr(
            self._upstream_client,
            "retry_initialize_session_with_legacy_schema",
            None,
        )
        if retry_method is None:
            return False

        try:
            tools = None
            if self._tooling_runtime is not None:
                tools = self._tooling_runtime.to_openai_tools()
            did_retry = await retry_method(tools=tools)
        except RealtimeClientError:
            return False
        except Exception:  # pragma: no cover - defensive fallback
            logger.exception("Unexpected failure retrying legacy session init schema fallback")
            return False
        return bool(did_retry)

    @staticmethod
    def _is_session_init_schema_error(*, code: str, message: str) -> bool:
        lower_code = code.strip().lower()
        lower_message = message.strip().lower()

        is_parameter_error = (
            "unknown_parameter" in lower_code
            or "invalid_parameter" in lower_code
            or "unknown parameter" in lower_message
            or "invalid parameter" in lower_message
        )
        if not is_parameter_error:
            return False

        schema_markers = (
            "session.",
            "input_audio_format",
            "output_audio_format",
            "turn_detection",
            "audio.input",
            "audio.output",
            "output_modalities",
        )
        return any(marker in lower_message for marker in schema_markers)

    def _is_expected_interrupt_race_error(self, *, code: str, message: str) -> bool:
        lower_code = code.strip().lower()
        lower_message = message.strip().lower()
        is_interrupt_cancel_race = lower_code == "response_cancel_not_active" or (
            "cancel" in lower_message and "no active response" in lower_message
        )
        if is_interrupt_cancel_race:
            return True

        is_active_response_race = lower_code == "conversation_already_has_active_response" or (
            "already" in lower_message and "active response" in lower_message
        )
        if not is_active_response_race:
            return False

        return (
            self._server_turn_detection_enabled
            or self._has_active_upstream_response
            or self._current_turn_response_started
            or self._tool_dispatcher.saw_duplicate_tool_call_event_for_turn
        )

    def client_end_turn_ignore_reason(self) -> str | None:
        if not self._server_turn_detection_enabled:
            return None
        if self._has_active_upstream_response:
            return "active_upstream_response"
        if self._current_turn_response_started:
            return "response_already_started_for_turn"
        return None

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

    def _schedule_inactivity_finalize(self) -> None:
        if not self._manual_turn_fallback_enabled:
            return
        if self._server_turn_detection_enabled:
            return
        if self._current_turn_response_started or self._has_active_upstream_response:
            return
        self._cancel_manual_finalize_task()
        self._manual_finalize_task = asyncio.create_task(
            self._run_inactivity_finalize_after_delay(),
            name=f"manual_finalize:{self._session_id}",
        )

    async def _run_inactivity_finalize_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._manual_turn_fallback_delay_s)
            await self._finalize_turn_if_needed_locked(reason="continuous_uplink_timeout")
        except asyncio.CancelledError:
            return
        except RealtimeClientError as exc:
            logger.warning("Inactivity finalize failed session=%s: %s", self._session_id, exc)
            await self._send_upstream_error(
                code="UPSTREAM_TURN_FINALIZE_FAILED",
                message=str(exc) or "Failed to finalize turn upstream",
                retriable=True,
            )
        except Exception:
            logger.exception("Unexpected inactivity finalize failure session=%s", self._session_id)
            await self._send_upstream_error(
                code="UPSTREAM_TURN_FINALIZE_FAILED",
                message="Failed to finalize turn upstream",
                retriable=True,
            )

    def _cancel_manual_finalize_task(self) -> None:
        task = self._manual_finalize_task
        self._manual_finalize_task = None
        if task is not None:
            task.cancel()

    async def _finalize_turn_if_needed_locked(self, *, reason: str) -> None:
        async with self._turn_finalize_lock:
            await self._finalize_turn_if_needed(reason=reason)

    async def _finalize_turn_if_needed(self, *, reason: str) -> None:
        if not self._manual_turn_fallback_enabled:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s manual_fallback=%s",
                    self._session_id,
                    reason,
                    self._manual_turn_fallback_enabled,
                )
            return

        if reason in {"continuous_uplink_timeout", "speech_stopped"} and self._server_turn_detection_enabled:
            return

        if reason == "client_end_turn":
            ignore_reason = self.client_end_turn_ignore_reason()
            if ignore_reason is not None:
                logger.debug(
                    "Ignoring client end_turn under server VAD session=%s reason=%s",
                    self._session_id,
                    ignore_reason,
                )
                return

        if not self._current_turn_audio_seen:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s audio_seen=%s",
                    self._session_id,
                    reason,
                    self._current_turn_audio_seen,
                )
            return
        if self._current_turn_response_started:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s response_started=%s",
                    self._session_id,
                    reason,
                    self._current_turn_response_started,
                )
            return
        if self._manual_response_sent_for_turn:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s manual_response_sent=%s",
                    self._session_id,
                    reason,
                    self._manual_response_sent_for_turn,
                )
            return
        if self._has_active_upstream_response:
            if reason == "client_end_turn":
                logger.debug(
                    "Skipping turn finalize session=%s reason=%s active_upstream_response=%s",
                    self._session_id,
                    reason,
                    self._has_active_upstream_response,
                )
            return

        if reason == "continuous_uplink_timeout":
            if self._server_vad_speaking:
                return
            if self._last_client_audio_at_monotonic is None:
                return
            inactivity = time.monotonic() - self._last_client_audio_at_monotonic
            if inactivity < self._manual_turn_fallback_delay_s:
                return

        self._audio_uplink.raise_if_failed()
        drained = await self._audio_uplink.wait_for_drain(timeout_seconds=1.5)
        if not drained:
            if not self._manual_finalize_error_sent_for_turn:
                self._manual_finalize_error_sent_for_turn = True
                await self._send_upstream_error(
                    code="UPSTREAM_AUDIO_FLUSH_TIMEOUT",
                    message="Timed out flushing client audio before turn finalization",
                    retriable=True,
                )
            return

        logger.info(
            "Manual turn finalize session=%s reason=%s queued_frames=%s sent_frames=%s",
            self._session_id,
            reason,
            self._audio_uplink.queue_size,
            self._audio_uplink.sent_count,
        )
        await self._upstream_client.send_json({"type": "input_audio_buffer.commit"})
        self._manual_response_sent_for_turn = True
        await self._send_response_create(source=f"manual_finalize:{reason}")

    async def _send_response_create(self, source: str) -> None:
        await self._upstream_client.send_json({"type": "response.create"})
        self._has_active_upstream_response = True
        self._current_turn_response_started = True
        self._cancel_manual_finalize_task()
        logger.info("Upstream response.create sent session=%s source=%s", self._session_id, source)

    def _reset_turn_state(self) -> None:
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._manual_finalize_error_sent_for_turn = False
        self._last_client_audio_at_monotonic = None
        self._current_response_id = None
        self._has_active_upstream_response = False
        self._tool_dispatcher.reset_turn_state()
        self._cancel_manual_finalize_task()

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
