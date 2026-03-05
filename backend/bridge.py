from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from backend.contracts import now_ms
from backend.frame_codec import SERVER_AUDIO_FRAME_TYPE
from backend.openai_realtime_client import (
    OpenAIRealtimeClient,
    RealtimeClientError,
)

logger = logging.getLogger(__name__)

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
    ) -> None:
        self._session_id = session_id
        self._upstream_client = upstream_client
        self._send_envelope = send_envelope
        self._send_binary_frame = send_binary_frame

        self._upstream_task: asyncio.Task[None] | None = None
        self._started_response_ids: set[str] = set()
        self._current_response_id: str | None = None
        self._manual_turn_fallback_enabled = manual_turn_fallback_enabled
        self._manual_turn_fallback_delay_s = (
            max(100, manual_turn_fallback_delay_ms) / 1000.0
        )
        self._manual_turn_fallback_task: asyncio.Task[None] | None = None
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._closed = False

    async def connect_and_start(self) -> None:
        await self._upstream_client.connect()
        await self._upstream_client.initialize_session()
        self._upstream_task = asyncio.create_task(
            self._run_upstream_loop(),
            name=f"upstream_loop:{self._session_id}",
        )

    async def append_client_audio(self, payload_bytes: bytes) -> None:
        if not payload_bytes:
            logger.debug(
                "Ignoring empty client audio payload for session=%s",
                self._session_id,
            )
            return

        await self._upstream_client.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(payload_bytes).decode("ascii"),
            }
        )
        self._current_turn_audio_seen = True
        self._schedule_manual_turn_finalize()

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
        self._cancel_manual_turn_finalize_task()

        with contextlib.suppress(RealtimeClientError):
            await self._upstream_client.close()

    async def _run_upstream_loop(self) -> None:
        try:
            async for event in self._upstream_client.iter_events():
                await self._handle_upstream_event(event)
        except asyncio.CancelledError:
            raise
        except RealtimeClientError as exc:
            logger.warning("Upstream loop closed for %s: %s", self._session_id, exc)
            await self._send_upstream_error(
                code="UPSTREAM_CONNECTION_ERROR",
                message="Upstream realtime connection failed",
                retriable=True,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.exception(
                "Unexpected upstream loop failure for %s", self._session_id
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
            self._cancel_manual_turn_finalize_task()
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
            self._cancel_manual_turn_finalize_task()
            await self._finalize_turn_if_needed(reason="speech_stopped")
            return

        if event_type == "response.created":
            logger.info("Upstream response.created session=%s", self._session_id)
            self._current_turn_response_started = True
            self._cancel_manual_turn_finalize_task()
            return

        if event_type == "session.created":
            logger.info("Upstream session.created session=%s", self._session_id)
            return

        if event_type == "session.updated":
            logger.info("Upstream session.updated session=%s", self._session_id)
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

        if response_id in self._started_response_ids:
            self._started_response_ids.remove(response_id)

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

        retriable = bool(err_payload.get("retriable", True))
        await self._send_upstream_error(code=code, message=message, retriable=retriable)

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

    def _schedule_manual_turn_finalize(self) -> None:
        if not self._manual_turn_fallback_enabled:
            return

        self._cancel_manual_turn_finalize_task()
        self._manual_turn_fallback_task = asyncio.create_task(
            self._run_manual_turn_finalize_timer(),
            name=f"manual_turn_finalize:{self._session_id}",
        )

    def _cancel_manual_turn_finalize_task(self) -> None:
        task = self._manual_turn_fallback_task
        self._manual_turn_fallback_task = None
        if task is None:
            return
        task.cancel()

    async def _run_manual_turn_finalize_timer(self) -> None:
        try:
            await asyncio.sleep(self._manual_turn_fallback_delay_s)
            await self._finalize_turn_if_needed(reason="idle_timeout")
        except asyncio.CancelledError:
            return

    async def _finalize_turn_if_needed(self, *, reason: str) -> None:
        if not self._manual_turn_fallback_enabled:
            return
        if not self._current_turn_audio_seen:
            return
        if self._current_turn_response_started:
            return
        if self._manual_response_sent_for_turn:
            return

        self._manual_response_sent_for_turn = True
        logger.info(
            "Manual turn finalize session=%s reason=%s",
            self._session_id,
            reason,
        )
        await self._upstream_client.send_json({"type": "input_audio_buffer.commit"})
        await self._upstream_client.send_json({"type": "response.create"})

    def _reset_turn_state(self) -> None:
        self._current_turn_audio_seen = False
        self._current_turn_response_started = False
        self._manual_response_sent_for_turn = False
        self._cancel_manual_turn_finalize_task()
