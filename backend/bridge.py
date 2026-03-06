from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
import wave
from collections.abc import Awaitable, Callable
from typing import Any

from backend.contracts import now_ms
from backend.frame_codec import SERVER_AUDIO_FRAME_TYPE
from backend.openai_realtime_client import (
    OpenAIRealtimeClient,
    RealtimeClientError,
)

logger = logging.getLogger(__name__)
SESSION_READY_EVENT_TYPES = {"session.created", "session.updated"}

EnvelopeSender = Callable[[str, dict[str, Any]], Awaitable[None]]
BinarySender = Callable[[int, int, bytes], Awaitable[None]]


class IOSRealtimeBridge:
    """Relay between iOS websocket transport and OpenAI realtime websocket."""

    def __init__(
        self,
        *,
        session_id: str,
        upstream_client: OpenAIRealtimeClient,
        send_envelope: EnvelopeSender,
        send_binary_frame: BinarySender,
        manual_turn_fallback_enabled: bool = True,
        manual_turn_fallback_delay_ms: int = 900,
        client_audio_queue_maxsize: int = 32,
        dump_input_audio_enabled: bool = False,
        dump_input_audio_dir: str = "backend/debug_audio",
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._send_envelope = send_envelope
        self._send_binary_frame = send_binary_frame

        self._upstream_task: asyncio.Task[None] | None = None
        self._client_audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=max(1, client_audio_queue_maxsize)
        )
        self._client_audio_sender_task: asyncio.Task[None] | None = None
        self._client_audio_sent_count = 0
        self._client_audio_dropped_oldest_count = 0
        self._client_audio_drop_log_step = 25
        self._started_response_ids: set[str] = set()
        self._last_stopped_response_id: str | None = None
        self._current_response_id: str | None = None
        self._manual_turn_fallback_enabled = manual_turn_fallback_enabled
        self._manual_turn_fallback_delay_s = (
            max(100, manual_turn_fallback_delay_ms) / 1000.0
        )
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._current_turn_started_at_monotonic: float | None = None
        self._dump_input_audio_enabled = dump_input_audio_enabled
        self._dump_input_audio_dir = dump_input_audio_dir
        self._input_audio_dump_writer: wave.Wave_write | None = None
        self._closed = False
        self._session_ready_confirmed = False
        self._session_ready_event = asyncio.Event()
        self._session_ready_error: tuple[str, str] | None = None

    async def connect_and_start(self) -> None:
        self._session_ready_confirmed = False
        self._session_ready_error = None
        self._session_ready_event.clear()
        await self._upstream_client.connect()
        self._ensure_client_audio_sender_task()
        self._upstream_task = asyncio.create_task(
            self._run_upstream_loop(),
            name=f"upstream_loop:{self._session_id}",
        )
        await self._upstream_client.initialize_session()
        await self._wait_for_upstream_session_ready()

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            logger.debug(
                "Ignoring empty client audio payload for session=%s",
                self._session_id,
            )
            return

        self._ensure_client_audio_sender_task()
        self._enqueue_client_audio(payload_bytes)
        self._append_input_audio_dump(payload_bytes)
        self._current_turn_audio_seen = True
        if self._current_turn_started_at_monotonic is None:
            self._current_turn_started_at_monotonic = time.monotonic()
        await self._finalize_turn_if_needed(reason="continuous_uplink_timeout")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        task = self._upstream_task
        self._upstream_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._shutdown_client_audio_sender()
        self._close_input_audio_dump_writer()
        with contextlib.suppress(RealtimeClientError):
            await self._upstream_client.close()

    def _ensure_client_audio_sender_task(self) -> None:
        task = self._client_audio_sender_task
        if task is not None and not task.done():
            return

        self._client_audio_sender_task = asyncio.create_task(
            self._run_client_audio_sender_loop(),
            name=f"client_audio_sender:{self._session_id}",
        )

    def _enqueue_client_audio(self, payload_bytes: bytes) -> None:
        while True:
            try:
                self._client_audio_queue.put_nowait(payload_bytes)
                return
            except asyncio.QueueFull:
                try:
                    self._client_audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    logger.debug(
                        "Client audio queue unexpectedly empty during overflow handling session=%s",
                        self._session_id,
                    )
                    continue
                else:
                    self._client_audio_queue.task_done()

                self._client_audio_dropped_oldest_count += 1
                drop_count = self._client_audio_dropped_oldest_count
                if (
                    drop_count == 1
                    or drop_count % self._client_audio_drop_log_step == 0
                ):
                    logger.warning(
                        "Client audio queue overflow session=%s policy=drop_oldest dropped=%s queue_max=%s",
                        self._session_id,
                        drop_count,
                        self._client_audio_queue.maxsize,
                    )

    async def _run_client_audio_sender_loop(self) -> None:
        try:
            while True:
                payload_bytes = await self._client_audio_queue.get()
                try:
                    if payload_bytes is None:
                        return
                    await self._upstream_client.send_json(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(payload_bytes).decode("ascii"),
                        }
                    )
                    self._client_audio_sent_count += 1
                finally:
                    self._client_audio_queue.task_done()
        except asyncio.CancelledError:
            raise
        except RealtimeClientError as exc:
            logger.warning(
                "Client audio sender closed for %s: %s",
                self._session_id,
                exc,
            )
        except Exception:
            logger.exception(
                "Unexpected client audio sender failure for %s",
                self._session_id,
            )

    async def _shutdown_client_audio_sender(self) -> None:
        task = self._client_audio_sender_task
        self._client_audio_sender_task = None
        if task is None:
            return

        if not task.done():
            enqueued_stop = False
            while not enqueued_stop:
                try:
                    self._client_audio_queue.put_nowait(None)
                    enqueued_stop = True
                except asyncio.QueueFull:
                    try:
                        self._client_audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    else:
                        self._client_audio_queue.task_done()
                        self._client_audio_dropped_oldest_count += 1
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        else:
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
            logger.exception(
                "Unexpected upstream loop failure for %s", self._session_id
            )
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

        if event_type in {"response.output_audio.done", "response.done"}:
            await self._on_response_done(event)
            self._reset_turn_state()
            return

        if event_type == "input_audio_buffer.speech_started":
            logger.info("Upstream VAD speech_started session=%s", self._session_id)
            await self._send_envelope("assistant.thinking", {"status": "thinking"})
            return

        if event_type == "input_audio_buffer.speech_stopped":
            logger.info("Upstream VAD speech_stopped session=%s", self._session_id)
            await self._finalize_turn_if_needed(reason="speech_stopped")
            return

        if event_type == "response.created":
            logger.info("Upstream response.created session=%s", self._session_id)
            self._current_turn_response_started = True
            return

        if event_type in SESSION_READY_EVENT_TYPES:
            logger.info("Upstream %s session=%s", event_type, self._session_id)
            self._mark_session_ready()
            return

        if event_type == "input_audio_buffer.committed":
            logger.info("Upstream input_audio_buffer.committed session=%s", self._session_id)
            return

        if event_type == "error":
            await self._on_upstream_error_event(event)
            return

        logger.debug("Unhandled upstream event type=%s", event_type)

    async def _on_audio_delta(self, event: dict[str, Any]) -> None:
        delta_b64 = event.get("delta")
        if not isinstance(delta_b64, str) or not delta_b64:
            return

        response_id = self._resolve_response_id(event)
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
            logger.warning(
                "Invalid base64 audio delta for session=%s", self._session_id
            )
            return

        await self._send_binary_frame(SERVER_AUDIO_FRAME_TYPE, now_ms(), pcm_bytes)

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

        if response_id in self._started_response_ids:
            self._started_response_ids.remove(response_id)

        self._last_stopped_response_id = response_id
        await self._send_envelope(
            "assistant.playback.control",
            {"command": "stop_response", "response_id": response_id},
        )

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

        if await self._maybe_retry_legacy_session_init(code=code, message=message):
            logger.warning(
                "Realtime session init schema mismatch recovered via legacy retry "
                "session=%s code=%s message=%s",
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
        ) or (
            "unknown parameter" in lower_message and "turn_detection" in lower_message
        ):
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
            await asyncio.wait_for(
                self._session_ready_event.wait(),
                timeout=timeout_seconds,
            )
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

    async def _maybe_retry_legacy_session_init(
        self, *, code: str, message: str
    ) -> bool:
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
            did_retry = await retry_method()
        except RealtimeClientError:
            return False
        except Exception:  # pragma: no cover - defensive fallback
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

    async def _finalize_turn_if_needed(self, *, reason: str) -> None:
        if not self._manual_turn_fallback_enabled:
            return
        if not self._current_turn_audio_seen:
            return
        if self._current_turn_response_started:
            return
        if self._manual_response_sent_for_turn:
            return
        if reason == "continuous_uplink_timeout":
            if self._current_turn_started_at_monotonic is None:
                return
            elapsed = time.monotonic() - self._current_turn_started_at_monotonic
            if elapsed < self._manual_turn_fallback_delay_s:
                return

        self._manual_response_sent_for_turn = True
        logger.info(
            "Manual turn finalize session=%s reason=%s",
            self._session_id,
            reason,
        )
        await self._wait_for_client_audio_queue_drain()
        await self._upstream_client.send_json({"type": "input_audio_buffer.commit"})
        await self._upstream_client.send_json({"type": "response.create"})

    async def _wait_for_client_audio_queue_drain(self) -> None:
        task = self._client_audio_sender_task
        if task is None or task.done():
            return

        try:
            await asyncio.wait_for(self._client_audio_queue.join(), timeout=1.5)
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out draining client audio queue session=%s pending=%s",
                self._session_id,
                self._client_audio_queue.qsize(),
            )

    def _reset_turn_state(self) -> None:
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._current_turn_started_at_monotonic = None
        self._current_response_id = None

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

    def _append_input_audio_dump(self, payload_bytes: bytes) -> None:
        if not self._dump_input_audio_enabled:
            return
        if not payload_bytes:
            return

        writer = self._input_audio_dump_writer
        if writer is None:
            writer = self._create_input_audio_dump_writer()
            if writer is None:
                return
            self._input_audio_dump_writer = writer

        try:
            writer.writeframes(payload_bytes)
        except Exception as exc:
            logger.warning(
                "Failed writing input audio dump session=%s: %s",
                self._session_id,
                exc,
            )

    def _create_input_audio_dump_writer(self) -> wave.Wave_write | None:
        try:
            os.makedirs(self._dump_input_audio_dir, exist_ok=True)
            file_path = os.path.join(
                self._dump_input_audio_dir,
                f"{self._session_id}_{now_ms()}.wav",
            )
            writer = wave.open(file_path, "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24_000)
            logger.info(
                "Input audio dump enabled session=%s path=%s",
                self._session_id,
                file_path,
            )
            return writer
        except Exception as exc:
            logger.warning(
                "Failed creating input audio dump writer session=%s: %s",
                self._session_id,
                exc,
            )
            return None

    def _close_input_audio_dump_writer(self) -> None:
        writer = self._input_audio_dump_writer
        self._input_audio_dump_writer = None
        if writer is None:
            return
        try:
            writer.close()
        except Exception as exc:
            logger.warning(
                "Failed closing input audio dump writer session=%s: %s",
                self._session_id,
                exc,
            )
